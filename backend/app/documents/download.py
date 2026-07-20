"""Streaming PDF downloader with strict origin, media, size, and hash policy."""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx


class DocumentDownloadError(RuntimeError):
    def __init__(self, code: str, detail: str, *, quarantined_path: Path | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.safe_detail = detail
        self.quarantined_path = quarantined_path


@dataclass(frozen=True, slots=True)
class DownloadedDocument:
    path: Path
    byte_size: int
    sha256: str
    media_type: str


class SafePdfDownloader:
    """Download allowlisted PDFs atomically without holding a full document in RAM."""

    _allowed_hosts = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})
    _pdf_types = frozenset({"application/pdf", "application/octet-stream"})

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        destination: Path,
        temporary: Path,
        quarantine: Path,
        maximum_bytes: int,
        chunk_bytes: int,
        concurrency: int,
        maximum_retries: int,
    ) -> None:
        if not 1 <= concurrency <= 3:
            raise ValueError("document download concurrency must be between 1 and 3")
        self._client = client
        self._destination = destination
        self._temporary = temporary
        self._quarantine = quarantine
        self._maximum_bytes = maximum_bytes
        self._chunk_bytes = chunk_bytes
        self._semaphore = asyncio.Semaphore(concurrency)
        self._maximum_retries = min(maximum_retries, 2)

    @classmethod
    def validate_url(cls, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in cls._allowed_hosts:
            raise DocumentDownloadError(
                "URL_NOT_ALLOWED", "PDF URL is not an approved HTTPS origin."
            )
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            raise DocumentDownloadError(
                "URL_NOT_ALLOWED", "PDF URL contains disallowed authority data."
            )

    async def download(self, url: str) -> DownloadedDocument:
        self.validate_url(url)
        async with self._semaphore:
            last_error: DocumentDownloadError | None = None
            for attempt in range(self._maximum_retries + 1):
                try:
                    return await self._download_once(url)
                except DocumentDownloadError as error:
                    last_error = error
                    if (
                        error.code not in {"NETWORK_ERROR", "UPSTREAM_ERROR"}
                        or attempt == self._maximum_retries
                    ):
                        raise
                    await asyncio.sleep(min(2**attempt, 2))
            assert last_error is not None
            raise last_error

    async def _download_once(self, url: str) -> DownloadedDocument:
        current_url = url
        for _ in range(3):
            try:
                async with self._client.stream(
                    "GET", current_url, headers={"Accept": "application/pdf"}
                ) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("Location")
                        if not location:
                            raise DocumentDownloadError(
                                "UPSTREAM_ERROR", "PDF redirect had no destination."
                            )
                        current_url = urljoin(current_url, location)
                        self.validate_url(current_url)
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        raise DocumentDownloadError(
                            "UPSTREAM_ERROR", "PDF source is temporarily unavailable."
                        )
                    if response.status_code != 200:
                        raise DocumentDownloadError(
                            "HTTP_ERROR", f"PDF source returned HTTP {response.status_code}."
                        )
                    return await self._stream_response(response)
            except httpx.TimeoutException as error:
                raise DocumentDownloadError("NETWORK_ERROR", "PDF download timed out.") from error
            except httpx.NetworkError as error:
                raise DocumentDownloadError(
                    "NETWORK_ERROR", "PDF source could not be reached."
                ) from error
        raise DocumentDownloadError("REDIRECT_LIMIT", "PDF source exceeded the redirect limit.")

    async def _stream_response(self, response: httpx.Response) -> DownloadedDocument:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if content_type not in self._pdf_types:
            raise DocumentDownloadError(
                "INVALID_MEDIA_TYPE", "Source did not return a PDF media type."
            )
        declared = response.headers.get("Content-Length")
        if declared and declared.isdecimal() and int(declared) > self._maximum_bytes:
            raise DocumentDownloadError(
                "DOCUMENT_TOO_LARGE", "PDF exceeds the configured size limit."
            )

        self._temporary.mkdir(parents=True, exist_ok=True)
        temporary_path = self._temporary / f"pdf-{os.urandom(12).hex()}.part"
        digest = hashlib.sha256()
        size = 0
        prefix = b""
        try:
            with temporary_path.open("xb") as output:
                async for chunk in response.aiter_bytes(self._chunk_bytes):
                    size += len(chunk)
                    if size > self._maximum_bytes:
                        raise DocumentDownloadError(
                            "DOCUMENT_TOO_LARGE", "PDF exceeds the configured size limit."
                        )
                    if len(prefix) < 5:
                        prefix += chunk[: 5 - len(prefix)]
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            sha256 = digest.hexdigest()
            if prefix != b"%PDF-":
                self._quarantine.mkdir(parents=True, exist_ok=True)
                quarantined = self._quarantine / f"{sha256}.invalid"
                os.replace(temporary_path, quarantined)
                raise DocumentDownloadError(
                    "INVALID_PDF_SIGNATURE",
                    "Downloaded content did not have a PDF signature and was quarantined.",
                    quarantined_path=quarantined,
                )
            self._destination.mkdir(parents=True, exist_ok=True)
            final_path = self._destination / f"{sha256}.pdf"
            if final_path.exists():
                temporary_path.unlink()
            else:
                os.replace(temporary_path, final_path)
            return DownloadedDocument(final_path, size, sha256, "application/pdf")
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
