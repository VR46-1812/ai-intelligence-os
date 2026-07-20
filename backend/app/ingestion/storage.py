"""Safe, atomic local persistence for untrusted raw source responses."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.ingestion.contracts import RawSourceRecord

_SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
_EXTENSIONS = {
    "application/atom+xml": ".xml",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
}


class RawPayloadError(RuntimeError):
    """Raised when a raw response cannot be persisted within safety policy."""


@dataclass(frozen=True, slots=True)
class StoredPayload:
    payload_sha256: str
    raw_payload_path: str
    metadata_path: str


class RawPayloadStore:
    """Persist immutable payload bytes and a deterministic provenance sidecar."""

    def __init__(self, data_root: Path, raw_root: Path, maximum_bytes: int) -> None:
        self._data_root = data_root.resolve(strict=False)
        self._raw_root = raw_root.resolve(strict=False)
        self._maximum_bytes = maximum_bytes
        if not self._raw_root.is_relative_to(self._data_root):
            raise RawPayloadError("raw payload root must remain inside the configured data root")
        if maximum_bytes <= 0:
            raise RawPayloadError("maximum raw payload bytes must be positive")

    @property
    def maximum_bytes(self) -> int:
        return self._maximum_bytes

    def persist(self, record: RawSourceRecord) -> StoredPayload:
        if not _SAFE_KEY.fullmatch(record.source_key):
            raise RawPayloadError("source key is unsafe for raw payload storage")
        if len(record.payload) > self._maximum_bytes:
            raise RawPayloadError("raw payload exceeds the configured byte limit")
        digest = hashlib.sha256(record.payload).hexdigest()
        observed = record.observed_at
        directory = (
            self._raw_root
            / record.source_key
            / f"{observed.year:04d}"
            / f"{observed.month:02d}"
            / f"{observed.day:02d}"
        ).resolve(strict=False)
        if not directory.is_relative_to(self._raw_root):
            raise RawPayloadError("resolved raw payload directory escaped its configured root")
        directory.mkdir(parents=True, exist_ok=True)
        extension = _EXTENSIONS.get(record.media_type.casefold(), ".bin")
        payload_path = directory / f"{digest}{extension}"
        metadata = json.dumps(
            {
                "schema_version": 1,
                "source_key": record.source_key,
                "upstream_id": record.upstream_id,
                "upstream_version": record.upstream_version,
                "canonical_url": record.canonical_url,
                "observed_at": record.observed_at.isoformat(),
                "published_at": None
                if record.published_at is None
                else record.published_at.isoformat(),
                "updated_at": None if record.updated_at is None else record.updated_at.isoformat(),
                "media_type": record.media_type,
                "payload_sha256": digest,
                "byte_size": len(record.payload),
                "response_metadata": record.response_metadata,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(metadata) > min(self._maximum_bytes, 1024 * 1024):
            raise RawPayloadError("raw response metadata exceeds the configured byte limit")
        metadata_digest = hashlib.sha256(metadata).hexdigest()[:16]
        metadata_path = directory / f"{digest}.{metadata_digest}.metadata.json"
        self._write_once(payload_path, record.payload)
        self._write_once(metadata_path, metadata)
        return StoredPayload(
            payload_sha256=digest,
            raw_payload_path=payload_path.relative_to(self._data_root).as_posix(),
            metadata_path=metadata_path.relative_to(self._data_root).as_posix(),
        )

    @staticmethod
    def _write_once(path: Path, content: bytes) -> None:
        if path.exists():
            if path.read_bytes() != content:
                raise RawPayloadError("immutable raw payload path has conflicting content")
            return
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except OSError as error:
            raise RawPayloadError("could not durably persist raw source response") from error
        finally:
            temporary.unlink(missing_ok=True)
