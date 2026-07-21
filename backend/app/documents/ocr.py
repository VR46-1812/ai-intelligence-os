"""Optional, disabled-by-default Tesseract boundary for OCR-required pages."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol
from uuid import uuid4


class OcrError(RuntimeError):
    """Safe OCR adapter failure."""


class OcrAdapter(Protocol):
    def extract_png(self, payload: bytes) -> str: ...


class TesseractAdapter:
    """Invoke a configured local Tesseract executable without a shell."""

    def __init__(
        self,
        *,
        executable: str,
        temporary_root: Path,
        language: str,
        timeout_seconds: int,
    ) -> None:
        self._executable = executable
        self._temporary_root = temporary_root
        self._language = language
        self._timeout_seconds = timeout_seconds

    def extract_png(self, payload: bytes) -> str:
        self._temporary_root.mkdir(parents=True, exist_ok=True)
        image_path = self._temporary_root / f"ocr-{uuid4().hex}.png"
        try:
            image_path.write_bytes(payload)
            result = subprocess.run(
                [self._executable, str(image_path), "stdout", "-l", self._language],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OcrError("Tesseract OCR was unavailable or timed out.") from error
        finally:
            image_path.unlink(missing_ok=True)
        if result.returncode != 0:
            raise OcrError("Tesseract OCR could not extract this page.")
        return " ".join(result.stdout.split())
