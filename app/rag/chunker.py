"""Policy document chunking.

Policy text is chunked by clause, not by an arbitrary token window, because a
citation must point at a clause ("CR-3.1"), not at "characters 900-1800 of
document 4". Section identifiers in the synthetic corpus are of the form
``PL-3.2`` and appear at the start of every ``##`` heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SECTION_HEADING_RE = re.compile(r"^(#{2,3})\s+(?P<body>.+?)\s*$")
SECTION_ID_RE = re.compile(r"^(?P<section>[A-Z]{2,5}-\d+(?:\.\d+)*)\s+(?P<title>.+)$")
FRONT_MATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)

MAX_CHUNK_CHARS = 1100
CHUNK_OVERLAP_CHARS = 120


@dataclass
class PolicyChunk:
    chunk_id: str
    document_id: str
    document_title: str
    policy_version: str
    section: str
    section_title: str
    text: str
    source_path: str
    chunk_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> Dict[str, Any]:
        base = {
            "document_id": self.document_id,
            "document_title": self.document_title,
            "policy_version": self.policy_version,
            "section": self.section,
            "section_title": self.section_title,
            "source_path": self.source_path,
            "chunk_index": self.chunk_index,
        }
        base.update({k: v for k, v in self.metadata.items() if isinstance(v, (str, int, float, bool))})
        return base


def parse_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    """Return (front matter mapping, remaining body)."""
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    body = match.group("body")
    remainder = text[match.end() :]
    data: Dict[str, Any] = {}
    try:
        import yaml  # noqa: PLC0415

        loaded = yaml.safe_load(body)
        if isinstance(loaded, dict):
            data = {str(k): v for k, v in loaded.items()}
    except Exception:  # pragma: no cover - fall back to a naive parse
        for line in body.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                data[key.strip()] = value.strip().strip('"')
    return data, remainder


def _split_long_text(text: str, max_chars: int, overlap: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [para.strip() for para in re.split(r"\n\s*\n", text) if para.strip()]
    pieces: List[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            pieces.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{paragraph}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_markdown(
    text: str,
    source_path: str,
    policy_version: Optional[str] = None,
    document_id: Optional[str] = None,
    document_title: Optional[str] = None,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> List[PolicyChunk]:
    """Chunk a synthetic policy markdown file by clause heading."""

    front, body = parse_front_matter(text)
    version = str(policy_version or front.get("policy_version") or "unknown")
    doc_id = str(document_id or front.get("document_id") or Path(source_path).stem)
    doc_title = str(document_title or front.get("document_title") or doc_id)

    sections: List[Tuple[str, str, List[str]]] = []
    current_section = "PREAMBLE"
    current_title = "Document preamble"
    buffer: List[str] = []

    for line in body.splitlines():
        heading = SECTION_HEADING_RE.match(line)
        if heading:
            heading_body = heading.group("body").strip().lstrip("#").strip()
            identified = SECTION_ID_RE.match(heading_body)
            if identified:
                if buffer:
                    sections.append((current_section, current_title, buffer))
                    buffer = []
                current_section = identified.group("section")
                current_title = identified.group("title").strip()
                continue
        buffer.append(line)
    if buffer:
        sections.append((current_section, current_title, buffer))

    chunks: List[PolicyChunk] = []
    for section, title, lines in sections:
        raw = "\n".join(lines).strip()
        if not raw:
            continue
        header = f"[{doc_id} {section} {title}] (DemoBank synthetic policy v{version})"
        for index, piece in enumerate(_split_long_text(raw, max_chars, overlap)):
            chunks.append(
                PolicyChunk(
                    chunk_id=f"v{version}|{doc_id}|{section}|{index}",
                    document_id=doc_id,
                    document_title=doc_title,
                    policy_version=version,
                    section=section,
                    section_title=title,
                    text=f"{header}\n{piece}".strip(),
                    source_path=source_path,
                    chunk_index=index,
                    metadata={
                        "status": str(front.get("status", "")),
                        "effective_from": str(front.get("effective_from", "")),
                        "effective_to": str(front.get("effective_to", "")),
                    },
                )
            )
    return chunks


def chunk_text(
    pages: List[Tuple[int, str]],
    source_path: str,
    policy_version: str,
    document_id: str,
    document_title: str,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap: int = CHUNK_OVERLAP_CHARS,
) -> List[PolicyChunk]:
    """Chunk non-markdown sources (for example an extracted PDF) by page."""
    chunks: List[PolicyChunk] = []
    for page_number, page_text in pages:
        cleaned = (page_text or "").strip()
        if not cleaned:
            continue
        for index, piece in enumerate(_split_long_text(cleaned, max_chars, overlap)):
            section = f"PAGE-{page_number}"
            chunks.append(
                PolicyChunk(
                    chunk_id=f"v{policy_version}|{document_id}|{section}|{index}",
                    document_id=document_id,
                    document_title=document_title,
                    policy_version=policy_version,
                    section=section,
                    section_title=f"Page {page_number}",
                    text=piece,
                    source_path=source_path,
                    chunk_index=index,
                )
            )
    return chunks
