"""Two reproducible, character-counted chunking strategies with source spans."""

from __future__ import annotations

import hashlib
import json


def _end_boundary(text: str, start: int, limit: int, minimum: int) -> int:
    if limit >= len(text):
        return len(text)
    # Prefer complete paragraphs, then lines, then sentence/word boundaries.
    for separator in ("\n\n", "\n", ". ", "; ", " "):
        position = text.rfind(separator, start + minimum, limit)
        if position >= 0:
            return position + len(separator)
    return limit


def build_chunks(dataset: dict, strategy: str, chunk_size: int = 1000,
                 overlap: int = 160) -> list[dict]:
    if strategy not in {"fixed", "recursive"}:
        raise ValueError("strategy must be fixed or recursive")
    if type(chunk_size) is not int or type(overlap) is not int:
        raise ValueError("chunk_size and overlap must be integers")
    if chunk_size < 20 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Require chunk_size >= 20 and 0 <= overlap < chunk_size")
    documents = {d["file_id"]: d for d in dataset["documents"]}
    chunks = []
    seen_evidence = set()
    for page in dataset["pages"]:
        if page.get("status", "passed") != "passed":
            continue
        evidence_id = page["evidence_id"]
        if evidence_id in seen_evidence:
            raise ValueError("Duplicate evidence_id in dataset")
        seen_evidence.add(evidence_id)
        document = documents.get(page["file_id"])
        if document is None:
            raise ValueError("Page refers to an unknown document")
        text = page["clean_text"]
        if not text.strip():
            continue
        start = 0
        while start < len(text):
            limit = min(len(text), start + chunk_size)
            end = limit if strategy == "fixed" else _end_boundary(
                text, start, limit, max(overlap + 1, chunk_size // 2))
            piece = text[start:end]
            if piece.strip():
                span = {
                    "evidence_id": evidence_id, "file_id": page["file_id"],
                    "pdf_page": page["pdf_page"], "section": page.get("section", ""),
                    "char_start": start, "char_end": end,
                }
                identity = {
                    "dataset": dataset.get("dataset_version", dataset.get("dataset_id")),
                    "strategy": strategy, "size": chunk_size, "overlap": overlap,
                    "span": span, "text": piece,
                }
                digest = hashlib.sha256(json.dumps(
                    identity, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
                chunks.append({
                    "chunk_id": "chunk_" + digest[:32], "text": piece,
                    "source_spans": [span],
                    "metadata": page.get("metadata", document.get("metadata", {})),
                    "title": document["title"], "file_id": page["file_id"],
                    "strategy": strategy, "chunk_config": {
                        "unit": "unicode_characters", "size": chunk_size,
                        "overlap": overlap, "table_rule": "page_text_with_row_boundaries",
                        "prefix_template": "none", "revision": "page-chunks-v1",
                    },
                })
            if end == len(text):
                break
            next_start = end - overlap
            if next_start <= start:
                raise ValueError("Chunk configuration does not advance")
            start = next_start
    if len({c["chunk_id"] for c in chunks}) != len(chunks):
        raise ValueError("Duplicate chunk IDs")
    return chunks
