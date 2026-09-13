import re
from dataclasses import dataclass
from pathlib import Path

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
SECTION_SPLIT_RE = re.compile(r"(?m)^## ")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    version: str
    effective_date: str
    section: str
    text: str


def _parse_front_matter(raw: str):
    match = FRONT_MATTER_RE.match(raw)
    if not match:
        return {}, raw
    meta_block, body = match.group(1), match.group(2)
    meta = {}
    for line in meta_block.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            meta[key.strip()] = value if value and value.lower() != "null" else None
    return meta, body


def load_policy_chunks(policy_dir: Path) -> list[Chunk]:
    """Loads every *.md file in policy_dir, splits it on "## " section
    headers, and tags each resulting chunk with the doc_id/version/
    effective_date declared in its YAML front-matter. Documents that update
    an earlier policy (e.g. a "v2" file that supersedes "v1") simply reuse
    the same section headings, which lets the retriever later prefer the
    newer version for any section that exists in both.
    """
    chunks: list[Chunk] = []
    for path in sorted(policy_dir.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(raw)
        doc_id = meta.get("doc_id") or path.stem
        version = meta.get("version") or "v1"
        effective_date = meta.get("effective_date") or "unknown"

        parts = SECTION_SPLIT_RE.split(body)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            lines = part.splitlines()
            title = lines[0].strip()
            text = "\n".join(lines[1:]).strip()
            if not text:
                continue
            chunk_id = f"{doc_id}:{version}:{title}"
            chunks.append(Chunk(chunk_id, doc_id, version, effective_date, title, text))
    return chunks
