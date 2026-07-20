"""Bounded asynchronous HTTP transport shared by source connectors."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from contextlib import suppress
from datetime import UTC, datetime

import httpx

from app.config import DownloadSettings, HttpSettings, ResourceBudgetSettings
from app.ingestion.contracts import (
    ConnectorErrorCode,
    ConnectorException,
    ConnectorFailure,
    HttpResponse,
)

Sleep = Callable[[float], Awaitable[None]]


class BoundedHttpClient:
    """Enforce timeouts, retries, rate spacing, size, media, and concurrency limits."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        maximum_retries: int,
        initial_backoff_seconds: float,
        maximum_backoff_seconds: float,
        maximum_response_bytes: int,
        concurrency: int,
        sleep: Sleep = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = lambda: 0.5,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        owns_client: bool = False,
    ) -> None:
        if not 0 <= maximum_retries <= 5:
            raise ValueError("maximum_retries must be between 0 and 5")
        if not 1 <= concurrency <= 3:
            raise ValueError("source HTTP concurrency must be between 1 and 3")
        if maximum_response_bytes <= 0:
            raise ValueError("maximum_response_bytes must be positive")
        self._client = client
        self._maximum_retries = maximum_retries
        self._initial_backoff_seconds = initial_backoff_seconds
        self._maximum_backoff_seconds = maximum_backoff_seconds
        self._maximum_response_bytes = maximum_response_bytes
        self._semaphore = asyncio.Semaphore(concurrency)
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter
        self._clock = clock
        self._owns_client = owns_client
        self._rate_locks: dict[str, asyncio.Lock] = {}
        self._last_request_at: dict[str, float] = {}

    @classmethod
    def from_settings(
        cls,
        http: HttpSettings,
        downloads: DownloadSettings,
        resources: ResourceBudgetSettings,
    ) -> BoundedHttpClient:
        timeout = httpx.Timeout(
            connect=http.connect_timeout_seconds,
            read=http.read_timeout_seconds,
            write=http.read_timeout_seconds,
            pool=http.connect_timeout_seconds,
        )
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": http.user_agent},
            limits=httpx.Limits(
                max_connections=resources.source_download_concurrency,
                max_keepalive_connections=resources.source_download_concurrency,
            ),
        )
        return cls(
            client,
            maximum_retries=http.maximum_retries,
            initial_backoff_seconds=http.initial_backoff_seconds,
            maximum_backoff_seconds=http.maximum_backoff_seconds,
            maximum_response_bytes=downloads.maximum_document_bytes,
            concurrency=resources.source_download_concurrency,
            owns_client=True,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        source_key: str,
        minimum_request_interval_ms: int,
        expected_media_types: Iterable[str],
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        expected = {media_type.casefold() for media_type in expected_media_types}
        if not expected:
            raise ValueError("at least one expected media type is required")
        attempts = min(3, self._maximum_retries + 1)
        for attempt in range(1, attempts + 1):
            await self._wait_for_rate_slot(source_key, minimum_request_interval_ms)
            failure: ConnectorFailure | None = None
            retry_after: str | None = None
            try:
                async with self._semaphore:
                    response = await self._request_once(url, headers)
                    try:
                        failure = self._classify_status(response, attempt)
                        if failure is None:
                            return await self._read_response(response, expected, attempt)
                        retry_after = response.headers.get("Retry-After")
                    finally:
                        await response.aclose()
            except httpx.TimeoutException as error:
                failure = ConnectorFailure(
                    code=ConnectorErrorCode.NETWORK_TIMEOUT,
                    retryable=True,
                    safe_message="source request timed out",
                    attempts=attempt,
                )
                if attempt == attempts:
                    raise ConnectorException(failure) from error
                await self._backoff(attempt, None)
                continue
            except httpx.RequestError as error:
                failure = ConnectorFailure(
                    code=ConnectorErrorCode.NETWORK_TIMEOUT,
                    retryable=True,
                    safe_message="source network request failed",
                    attempts=attempt,
                )
                if attempt == attempts:
                    raise ConnectorException(failure) from error
                await self._backoff(attempt, None)
                continue
            if failure.retryable and attempt < attempts:
                await self._backoff(attempt, retry_after)
                continue
            raise ConnectorException(failure)
        raise AssertionError("bounded retry loop exited unexpectedly")

    async def _request_once(self, url: str, headers: dict[str, str] | None) -> httpx.Response:
        request = self._client.build_request("GET", url, headers=headers)
        return await self._client.send(request, stream=True)

    async def _read_response(
        self, response: httpx.Response, expected: set[str], attempt: int
    ) -> HttpResponse:
        media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        if media_type not in expected:
            raise ConnectorException(
                ConnectorFailure(
                    code=ConnectorErrorCode.UNSUPPORTED_MEDIA,
                    retryable=False,
                    safe_message="source returned an unsupported media type",
                    attempts=attempt,
                    status_code=response.status_code,
                )
            )
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError as error:
                raise ConnectorException(
                    ConnectorFailure(
                        code=ConnectorErrorCode.INVALID_RESPONSE,
                        retryable=False,
                        safe_message="source returned an invalid content length",
                        attempts=attempt,
                        status_code=response.status_code,
                    )
                ) from error
            if declared_size < 0:
                raise ConnectorException(
                    ConnectorFailure(
                        code=ConnectorErrorCode.INVALID_RESPONSE,
                        retryable=False,
                        safe_message="source returned a negative content length",
                        attempts=attempt,
                        status_code=response.status_code,
                    )
                )
            if declared_size > self._maximum_response_bytes:
                raise self._too_large(attempt, response.status_code)

        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > self._maximum_response_bytes:
                raise self._too_large(attempt, response.status_code)
        metadata = {
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "retrieved_at": self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        await response.aclose()
        return HttpResponse(
            status_code=response.status_code,
            media_type=media_type,
            content=bytes(content),
            response_metadata={key: value for key, value in metadata.items() if value is not None},
        )

    @staticmethod
    def _classify_status(response: httpx.Response, attempt: int) -> ConnectorFailure | None:
        status = response.status_code
        if 200 <= status <= 299:
            return None
        if status in {401, 403}:
            return ConnectorFailure(
                code=ConnectorErrorCode.AUTH_REQUIRED,
                retryable=False,
                safe_message="source authentication was rejected",
                attempts=attempt,
                status_code=status,
            )
        if status == 429:
            return ConnectorFailure(
                code=ConnectorErrorCode.RATE_LIMITED,
                retryable=True,
                safe_message="source rate limit was reached",
                attempts=attempt,
                status_code=status,
            )
        if status == 408:
            return ConnectorFailure(
                code=ConnectorErrorCode.NETWORK_TIMEOUT,
                retryable=True,
                safe_message="source request timed out upstream",
                attempts=attempt,
                status_code=status,
            )
        if status >= 500:
            return ConnectorFailure(
                code=ConnectorErrorCode.UPSTREAM_5XX,
                retryable=True,
                safe_message="source returned a server error",
                attempts=attempt,
                status_code=status,
            )
        return ConnectorFailure(
            code=ConnectorErrorCode.INVALID_RESPONSE,
            retryable=False,
            safe_message="source rejected the request",
            attempts=attempt,
            status_code=status,
        )

    def _too_large(self, attempt: int, status_code: int) -> ConnectorException:
        return ConnectorException(
            ConnectorFailure(
                code=ConnectorErrorCode.CONTENT_TOO_LARGE,
                retryable=False,
                safe_message="source response exceeded the configured byte limit",
                attempts=attempt,
                status_code=status_code,
            )
        )

    async def _wait_for_rate_slot(self, source_key: str, interval_ms: int) -> None:
        if interval_ms < 0:
            raise ValueError("minimum request interval cannot be negative")
        lock = self._rate_locks.setdefault(source_key, asyncio.Lock())
        async with lock:
            now = self._monotonic()
            last_request = self._last_request_at.get(source_key)
            delay = (
                0.0 if last_request is None else max(0.0, last_request + interval_ms / 1000 - now)
            )
            if delay:
                await self._sleep(delay)
            self._last_request_at[source_key] = self._monotonic()

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(
            self._maximum_backoff_seconds,
            self._initial_backoff_seconds * (2 ** (attempt - 1)),
        )
        if retry_after is not None:
            with suppress(ValueError):
                delay = min(self._maximum_backoff_seconds, max(delay, float(retry_after)))
        jittered = delay * (0.5 + 0.5 * min(1.0, max(0.0, self._jitter())))
        await self._sleep(jittered)
