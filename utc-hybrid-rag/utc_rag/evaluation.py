"""Reproducible retrieval evaluation against source-level evidence, not answers."""

from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _evidence_key(evidence: dict[str, Any]) -> tuple:
    if evidence.get("char_start") is not None and evidence.get("char_end") is not None:
        locator = ("span", evidence["char_start"], evidence["char_end"])
    else:
        locator = ("quote", _normalized(evidence.get("quote", "")))
    return (evidence["file_id"], evidence["pdf_page"], *locator)


def _validate_evidence(evidence: dict[str, Any]) -> None:
    if not isinstance(evidence.get("file_id"), str) or not evidence["file_id"]:
        raise ValueError("Gold evidence requires a nonempty file_id")
    page = evidence.get("pdf_page")
    if type(page) is not int or page < 1:
        raise ValueError("Gold pdf_page must be a positive 1-based integer")
    start, end = evidence.get("char_start"), evidence.get("char_end")
    has_span = start is not None or end is not None
    if has_span and (type(start) is not int or type(end) is not int or start < 0 or end <= start):
        raise ValueError("Gold character span must be a nonempty half-open interval")
    quote = evidence.get("quote")
    if quote is not None and (not isinstance(quote, str) or not _normalized(quote)):
        raise ValueError("Gold quote must be nonempty text")
    if not has_span and not quote:
        raise ValueError("Gold evidence needs a character span or an exact source quote")


def covers_evidence(chunk: dict[str, Any], evidence: dict[str, Any]) -> bool:
    """Require a source locator plus complete span coverage or a literal quote.

    Quote comparison preserves Vietnamese accents, case and punctuation; only
    Unicode NFC and whitespace are normalized. A multi-page chunk must expose
    text per source span for quote-only attribution; otherwise offsets are used.
    """
    spans = chunk.get("source_spans", [])
    pages = {(s.get("file_id"), s.get("pdf_page")) for s in spans}
    for span in spans:
        if (span.get("file_id"), span.get("pdf_page")) != (evidence["file_id"], evidence["pdf_page"]):
            continue
        start, end = evidence.get("char_start"), evidence.get("char_end")
        if start is not None and end is not None:
            span_start, span_end = span.get("char_start"), span.get("char_end")
            if span_start is not None and span_end is not None and span_start <= start and span_end >= end:
                return True
        quote = evidence.get("quote")
        span_text = span.get("text")
        if span_text is None and len(pages) == 1:
            span_text = chunk.get("text", "")
        if quote and span_text is not None and _normalized(quote) in _normalized(span_text):
            return True
    return False


def score_query(
    chunks: list[dict[str, Any]],
    results: list[dict[str, Any]],
    gold_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute spec metrics, with missing Top-3 positions counted as misses."""
    unique_gold = {_evidence_key(unit): unit for unit in gold_evidence}
    gold = list(unique_gold.values())
    relevant = {
        chunk["chunk_id"]
        for chunk in chunks
        if any(covers_evidence(chunk, unit) for unit in gold)
    }
    by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    if len(by_id) != len(chunks):
        raise ValueError("Duplicate chunk IDs in evaluation bundle")
    top3: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        chunk_id = result["chunk_id"]
        if chunk_id not in by_id:
            raise ValueError(f"Retrieval returned unknown chunk ID: {chunk_id}")
        if chunk_id in seen:
            raise ValueError(f"Retrieval returned duplicate chunk ID: {chunk_id}")
        seen.add(chunk_id)
        top3.append(by_id[chunk_id])
        if len(top3) == 3:
            break
    hits = len({chunk["chunk_id"] for chunk in top3} & relevant)
    covered = [i for i, unit in enumerate(gold) if any(covers_evidence(chunk, unit) for chunk in top3)]
    mapped = [i for i, unit in enumerate(gold) if any(covers_evidence(chunk, unit) for chunk in chunks)]
    return {
        "precision_at_3": hits / 3,
        "recall_at_3": hits / len(relevant) if relevant else None,
        "evidence_coverage_at_3": len(covered) / len(gold) if gold else None,
        "top3_chunk_ids": [chunk["chunk_id"] for chunk in top3],
        "relevant_chunk_ids": sorted(relevant),
        "relevant_chunk_count": len(relevant),
        "hits_at_3": hits,
        "gold_evidence_count": len(gold),
        "covered_evidence_indices": covered,
        "unmapped_evidence_indices": [i for i in range(len(gold)) if i not in mapped],
        "retrieval_scored": bool(gold),
    }


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if row["retrieval_scored"] and row[key] is not None]
    return math.fsum(values) / len(values) if values else None


def evaluate(manager: Any, questions_path: Path, output_dir: Path) -> dict[str, Any]:
    """Compare the current fixed/recursive bundles; write a JSON/CSV report.

    This measures retrieval only. Expected response statuses and answer quality
    remain review inputs and are never marked passed by retrieval scores.
    """
    questions_path, output_dir = Path(questions_path), Path(output_dir)
    question_set = json.loads(questions_path.read_text(encoding="utf-8"))
    questions = question_set.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("Question set must contain a nonempty questions list")
    categories = ("keyword", "semantic", "synthesis", "diagnostic")
    seen_ids: set[str] = set()
    counts = {category: 0 for category in categories}
    for question in questions:
        qid = question.get("id")
        if not isinstance(qid, str) or not qid or qid in seen_ids:
            raise ValueError("Question IDs must be nonempty and unique")
        seen_ids.add(qid)
        if not isinstance(question.get("query"), str) or not question["query"].strip():
            raise ValueError(f"Question {qid} needs nonempty query text")
        category = question.get("category")
        if category not in categories:
            raise ValueError(f"Unknown question category: {category}")
        counts[category] += 1
        if not isinstance(question.get("filters", {}), dict):
            raise ValueError(f"Question {qid} filters must be an object")
        evidence = question.get("evidence", [])
        if not isinstance(evidence, list) or (category != "diagnostic" and not evidence):
            raise ValueError(f"Question {qid} needs source-level gold evidence")
        for unit in evidence:
            _validate_evidence(unit)

    current = manager.current()
    if not current.get("fixed") or not current.get("recursive"):
        raise ValueError("Evaluation requires both fixed and recursive published bundles")
    bundles = {strategy: manager.load(current[strategy]) for strategy in ("fixed", "recursive")}
    manifests = {strategy: bundle.manifest for strategy, bundle in bundles.items()}
    datasets = {manifest.get("dataset_version") for manifest in manifests.values()}
    if len(datasets) != 1 or None in datasets:
        raise ValueError("Chunking comparison must use the same recorded dataset_version")
    dataset = next(iter(datasets))
    if question_set.get("dataset_version") != dataset:
        raise ValueError("Question ground truth dataset_version does not match the bundles")
    embedding = manifests["fixed"].get("embedding")
    if not embedding or embedding != manifests["recursive"].get("embedding"):
        raise ValueError("Chunking comparison must use the same recorded embedding contract")

    from .retrieval import retrieve

    rows: list[dict[str, Any]] = []
    for strategy, bundle in bundles.items():
        for question in questions:
            retrieved = retrieve(bundle, question["query"], question.get("filters", {}), top_k=3)
            if retrieved.get("status") in ("error", "retrieval_incomplete"):
                raise ValueError(f"Incomplete retrieval for {question['id']} ({strategy})")
            metrics = score_query(bundle.chunks, retrieved["results"], question.get("evidence", []))
            rows.append({
                "question_id": question["id"],
                "category": question["category"],
                "query": question["query"],
                "filters": question.get("filters", {}),
                "strategy": strategy,
                "bundle_id": current[strategy],
                "expected_status": question.get("expected_status"),
                "response_status_evaluated": False,
                **metrics,
            })

    summaries = {}
    for strategy in bundles:
        own_rows = [row for row in rows if row["strategy"] == strategy]
        summaries[strategy] = {
            "questions": len(own_rows),
            "questions_with_gold_evidence": sum(row["retrieval_scored"] for row in own_rows),
            "unmapped_evidence_units": sum(len(row["unmapped_evidence_indices"]) for row in own_rows),
            **{key: _mean(own_rows, key) for key in (
                "precision_at_3", "recall_at_3", "evidence_coverage_at_3"
            )},
        }
    report = {
        "evaluation_schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_kind": "retrieval_only",
        "dataset_version": dataset,
        "question_set": str(questions_path.resolve()),
        "question_set_split": question_set.get("split", "unspecified"),
        "ground_truth_review_status": question_set.get("ground_truth_review_status", "not_recorded"),
        "category_counts": counts,
        "minimum_category_counts_met": all(counts[category] >= 5 for category in categories[:3]),
        "bundles": manifests,
        "conditions": {
            "top_k": 3,
            "precision_denominator": 3,
            "recall_unit": "strategy_specific_relevant_chunk",
            "evidence_coverage_unit": "unique_source_evidence",
            "relevance_rule": "same file/page and full half-open character span coverage OR exact quote after NFC/whitespace normalization",
            "multipage_quote_rule": "requires per-source-span text; no quote attribution from combined multi-page text",
            "unmapped_gold_rule": "reported explicitly; recall is null when there are no relevant chunks",
            "diagnostic_rule": "no-gold diagnostics excluded from aggregate retrieval metrics; expected answer status is not evaluated",
            "answer_quality_evaluated": False,
        },
        "summary": summaries,
        "results": rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "retrieval_metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    fields = ["question_id", "category", "strategy", "bundle_id", "precision_at_3", "recall_at_3",
              "evidence_coverage_at_3", "relevant_chunk_count", "gold_evidence_count",
              "top3_chunk_ids", "unmapped_evidence_indices", "expected_status", "response_status_evaluated"]
    with (output_dir / "retrieval_metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            csv_row = dict(row)
            for field in ("top3_chunk_ids", "unmapped_evidence_indices"):
                csv_row[field] = json.dumps(row[field], ensure_ascii=False)
            writer.writerow(csv_row)
    return report
