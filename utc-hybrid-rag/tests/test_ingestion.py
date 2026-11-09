import json
from pathlib import Path

import pymupdf
import pytest

from utc_rag.chunking import build_chunks
from utc_rag.ingestion import Ingestor


def make_pdf(path: Path, text="Major code 7480201. Skills and learning outcomes.", blank=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        if not blank:
            page.insert_text((60, 70), text)
        pdf.save(path)


def dataset_for(data, job):
    return json.loads((data / job["dataset_path"]).read_text(encoding="utf-8"))


def test_duplicate_bytes_same_name_different_bytes_and_resume(tmp_path):
    source, data = tmp_path / "source", tmp_path / "data"
    make_pdf(source / "a" / "source.pdf")
    (source / "b").mkdir()
    (source / "b" / "source.pdf").write_bytes((source / "a" / "source.pdf").read_bytes())
    make_pdf(source / "c" / "source.pdf", "Different content; preserve this version.")
    ingestor = Ingestor(data)
    job = ingestor.ingest(source)
    dataset = dataset_for(data, job)
    assert len(dataset["documents"]) == 2
    assert len(dataset["source_occurrences"]) == 3
    assert dataset["coverage"]["duplicate_files"] == 1
    assert len({d["snapshot_path"] for d in dataset["documents"]}) == 2
    assert all(d["logical_document_id"] is None for d in dataset["documents"])
    rerun = ingestor.ingest(source)
    assert rerun["job_id"] == job["job_id"] and rerun["reused"]
    snapshot = data / dataset["documents"][0]["snapshot_path"]
    snapshot.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        ingestor.ingest(source)


def test_word_archive_blank_and_scan_are_accounted_for(tmp_path):
    source, data = tmp_path / "source", tmp_path / "data"
    source.mkdir()
    (source / "word.docx").write_bytes(b"unconverted word")
    (source / "archive.rar").write_bytes(b"rar unavailable")
    make_pdf(source / "blank.pdf", blank=True)
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        pix.clear_with(255)
        page.insert_image(page.rect, pixmap=pix)
        pdf.save(source / "scan.pdf")
    job = Ingestor(data).ingest(source)
    dataset = dataset_for(data, job)
    assert dataset["coverage"]["total_files"] == 4
    assert dataset["coverage"]["blocked_files"] == 4
    assert dataset["pages"] == []
    assert {p["classification"] for p in dataset["page_inventory"]} == {"image", "blank_unverified"}
    assert all(p["status"] == "needs_review" for p in dataset["page_inventory"])
    assert job["status"] == "completed_with_issues"


def test_metadata_requires_source_evidence_and_stays_at_page_scope(tmp_path):
    source, data = tmp_path / "source", tmp_path / "data"
    source.mkdir()
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((60, 70), "Major 7480201 computer science")
        pdf.new_page().insert_text((60, 70), "Major 7520103 mechanical engineering")
        pdf.save(source / "multi.pdf")
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"files": {"multi.pdf": {
        "fields": {"cohort_from": {"value": "2024", "verification_status": "verified", "pages": [1]}},
        "page_overrides": {
            "1": {"fields": {"major_code": {"value": "7480201", "verification_status": "verified", "pages": [1]}}},
            "2": {"fields": {"major_code": {"value": "7520103", "verification_status": "verified", "pages": [2]}}},
        }
    }}}), encoding="utf-8")
    dataset = dataset_for(data, Ingestor(data).ingest(source, metadata))
    assert dataset["documents"][0]["metadata"]["cohort_from"]["verification_status"] == "unverified"
    assert dataset["documents"][0]["metadata"]["major_code"]["value"] is None
    assert [p["metadata"]["major_code"]["value"] for p in dataset["pages"]] == ["7480201", "7520103"]
    assert all(p["metadata"]["major_code"]["verification_status"] == "verified" for p in dataset["pages"])


def test_chunk_strategies_map_every_character_without_truncation():
    text = "First section. Important exception.\n\n" * 20 + "Final condition must stay."
    dataset = {"dataset_version": "dataset_test", "documents": [
        {"file_id": "f1", "title": "Source", "metadata": {}}], "pages": [
        {"evidence_id": "ev1", "file_id": "f1", "pdf_page": 2,
         "section": "A", "clean_text": text, "status": "passed"}]}
    outputs = {}
    for strategy in ("fixed", "recursive"):
        chunks = build_chunks(dataset, strategy, chunk_size=120, overlap=20)
        assert chunks == build_chunks(dataset, strategy, chunk_size=120, overlap=20)
        covered = set()
        for chunk in chunks:
            span = chunk["source_spans"][0]
            assert chunk["text"] == text[span["char_start"]:span["char_end"]]
            assert span["pdf_page"] == 2
            assert len(chunk["text"]) <= 120
            covered.update(range(span["char_start"], span["char_end"]))
        assert covered == set(range(len(text)))
        outputs[strategy] = chunks
    assert [c["text"] for c in outputs["fixed"]] != [c["text"] for c in outputs["recursive"]]
    with pytest.raises(ValueError):
        build_chunks(dataset, "fixed", chunk_size=100, overlap=100)


def test_unknown_page_evidence_cannot_verify_metadata(tmp_path):
    source, data = tmp_path / "source", tmp_path / "data"
    make_pdf(source / "one.pdf")
    metadata = tmp_path / "meta.json"
    metadata.write_text(json.dumps({"files": {"one.pdf": {"fields": {
        "major_code": {"value": "7480201", "verification_status": "verified", "pages": [999]}
    }}}}), encoding="utf-8")
    dataset = dataset_for(data, Ingestor(data).ingest(source, metadata))
    assert dataset["documents"][0]["metadata"]["major_code"]["verification_status"] == "unverified"


def test_source_must_not_contain_output_directory(tmp_path):
    with pytest.raises(ValueError, match="contain"):
        Ingestor(tmp_path / "data").ingest(tmp_path)
