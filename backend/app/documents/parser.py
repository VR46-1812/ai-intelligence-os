"""Bounded page-preserving PDF text extraction using PyMuPDF."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pymupdf

_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\s+)?[A-Z][A-Za-z0-9 ,:()/-]{2,80}$")


class PdfParseError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.safe_detail = detail


@dataclass(frozen=True, slots=True)
class ParsedSpan:
    page: int
    char_start: int
    char_end: int
    text: str
    section_path: str | None
    normalized_sha256: str
    metadata: dict[str, int | str]


@dataclass(frozen=True, slots=True)
class ParsedPdf:
    page_count: int
    spans: tuple[ParsedSpan, ...]
    empty_pages: tuple[int, ...]
    partial: bool


class PdfTextExtractor:
    parser_name = "PyMuPDF"

    def __init__(self, *, maximum_pages: int = 500, maximum_page_characters: int = 100_000) -> None:
        self._maximum_pages = maximum_pages
        self._maximum_page_characters = maximum_page_characters

    def extract(self, path: Path) -> ParsedPdf:
        try:
            document = pymupdf.open(path)
        except (pymupdf.FileDataError, OSError) as error:
            raise PdfParseError("MALFORMED_PDF", "PDF could not be opened safely.") from error
        with document:
            if document.needs_pass:
                raise PdfParseError("ENCRYPTED_PDF", "Encrypted PDFs are not parsed.")
            page_count = int(document.page_count)
            pages_to_read = min(page_count, self._maximum_pages)
            spans: list[ParsedSpan] = []
            empty_pages: list[int] = []
            section: str | None = None
            for page_index in range(pages_to_read):
                try:
                    blocks = cast(
                        list[tuple[float, float, float, float, str, int, int]],
                        document[page_index].get_text("blocks", sort=True),
                    )
                except RuntimeError as error:
                    raise PdfParseError(
                        "PAGE_EXTRACTION_FAILED", "A PDF page could not be extracted."
                    ) from error
                position = 0
                page_has_text = False
                for block in blocks:
                    text = " ".join(str(block[4]).split())
                    if not text:
                        continue
                    page_has_text = True
                    text = text[: self._maximum_page_characters - position]
                    if not text:
                        break
                    if _HEADING.fullmatch(text) and len(text.split()) <= 12:
                        section = text
                    normalized = " ".join(text.casefold().split())
                    spans.append(
                        ParsedSpan(
                            page=page_index + 1,
                            char_start=position,
                            char_end=position + len(text),
                            text=text,
                            section_path=section,
                            normalized_sha256=hashlib.sha256(normalized.encode()).hexdigest(),
                            metadata={"block": int(block[5]), "parser_order": len(spans)},
                        )
                    )
                    position += len(text) + 1
                if not page_has_text:
                    empty_pages.append(page_index + 1)
            return ParsedPdf(
                page_count=page_count,
                spans=tuple(spans),
                empty_pages=tuple(empty_pages),
                partial=page_count > self._maximum_pages or bool(empty_pages),
            )
