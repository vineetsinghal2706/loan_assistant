"""Document parsers for uploaded applicant packs.

PDF text is extracted with PyMuPDF when available; markdown, text, CSV and JSON
are handled natively. Page numbers are preserved so every extracted field can
carry provenance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".log"}
JSON_SUFFIXES = {".json"}
PDF_SUFFIXES = {".pdf"}
MAX_BYTES = 8 * 1024 * 1024


class UnsupportedDocument(ValueError):
    """Raised when a document cannot be parsed."""


@dataclass
class ParsedDocument:
    name: str
    parser: str
    pages: List[Tuple[int, str]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(page_text for _, page_text in self.pages)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def character_count(self) -> int:
        return len(self.text)


def _parse_pdf(name: str, data: bytes) -> ParsedDocument:
    try:
        import fitz  # PyMuPDF  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - environment dependent
        raise UnsupportedDocument(
            "PyMuPDF is not installed, so PDF documents cannot be parsed. "
            "Install PyMuPDF or upload a text/markdown version."
        ) from exc

    pages: List[Tuple[int, str]] = []
    with fitz.open(stream=data, filetype="pdf") as document:
        for index, page in enumerate(document, start=1):
            pages.append((index, page.get_text() or ""))
    doc = ParsedDocument(name=name, parser="pymupdf", pages=pages)
    if not doc.text.strip():
        doc.warnings.append(
            "No selectable text was found. The PDF may be a scan; OCR is out of scope "
            "for this educational demo."
        )
    return doc


def _parse_json(name: str, data: bytes) -> ParsedDocument:
    payload = json.loads(data.decode("utf-8", errors="replace"))
    lines: List[str] = []

    def walk(key: str, node: object) -> None:
        """Flatten to ``label: value`` lines the field extractor can read."""
        if isinstance(node, dict):
            for child_key, value in node.items():
                walk(str(child_key), value)
        elif isinstance(node, list):
            for item in node:
                walk(key, item)
        else:
            lines.append(f"{key}: {node}" if key else str(node))

    walk("", payload)
    return ParsedDocument(name=name, parser="json", pages=[(1, "\n".join(lines))])


def parse_bytes(name: str, data: bytes) -> ParsedDocument:
    if len(data) > MAX_BYTES:
        raise UnsupportedDocument(
            f"{name} is larger than the {MAX_BYTES // (1024 * 1024)} MB demo limit."
        )
    suffix = Path(name).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return _parse_pdf(name, data)
    if suffix in JSON_SUFFIXES:
        return _parse_json(name, data)
    if suffix in TEXT_SUFFIXES or not suffix:
        text = data.decode("utf-8", errors="replace")
        return ParsedDocument(name=name, parser="text", pages=[(1, text)])
    raise UnsupportedDocument(
        f"Unsupported file type '{suffix}'. Supported: PDF, TXT, MD, CSV, TSV, JSON."
    )


def parse_path(path: Path) -> ParsedDocument:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    return parse_bytes(path.name, path.read_bytes())
