"""Check the OCR and Word reference review boundary through ingestion and chunking."""

import json
from pathlib import Path

import pymupdf
import pytest

from utc_rag import ocr
from utc_rag.bundles import source_checksums
from utc_rag.chunking import build_chunks
from utc_rag.ingestion import Ingestor
from utc_rag.storage import digest


OCR_EXCERPT = "Mã ngành: 7.48.02.01 (IE3)"
OCR_TEXT = f"Chuẩn đầu ra ngành Công nghệ thông tin.\n  {OCR_EXCERPT}\nThời gian đào tạo: chưa đối chiếu."
OCR_CLEAN_TEXT = f"Chuẩn đầu ra ngành Công nghệ thông tin.\n {OCR_EXCERPT}\nThời gian đào tạo: chưa đối chiếu."
OCR_BLOCK = {"bbox": [60.0, 48.0, 274.0, 72.0], "text": OCR_TEXT,
             "block_number": 0, "block_type": 0}


def make_pdf(path: Path, *, scan: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        page = document.new_page(width=390, height=240)
        if scan:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
            pixmap.clear_with(255)
            page.insert_image(page.rect, pixmap=pixmap)
        else:
            page.insert_text((60, 70), "Reference page for the approved Word source.")
        document.save(path)


def read_dataset(data: Path, job: dict) -> dict:
    return json.loads((data / job["dataset_path"]).read_text(encoding="utf-8"))


def write_sidecar(path: Path, filename: str, review: dict) -> Path:
    path.write_text(json.dumps({"files": {filename: review}}), encoding="utf-8")
    return path


@pytest.fixture
def ocr_calls(monkeypatch):
    calls = []

    class FakeEngine:
        contract = {"revision": "test-pdf-ocr", "language": "vie+eng"}

        def __init__(self, _tessdata):
            pass

        def recognize(self, page, *, dpi):
            calls.append((page.number, dpi))
            return {"text": OCR_TEXT, "blocks": [OCR_BLOCK], "bbox": list(page.rect),
                    "coordinate_system": ocr.COORDINATE_SYSTEM, "engine": "test-engine",
                    "language": "vie+eng", "dpi": dpi, "confidence": None}

    monkeypatch.setattr(ocr, "OCREngine", FakeEngine)
    return calls


def test_ocr_review_requires_exact_candidate_and_reuses_cached_recognition(tmp_path, ocr_calls):
    source, data = tmp_path / "scan.pdf", tmp_path / "data"
    make_pdf(source, scan=True)
    original_sha = digest(source)
    ingestor = Ingestor(data)
    pending = read_dataset(data, ingestor.ingest(source, ocr=True))
    page = pending["raw_pages"][0]
    candidate = page["ocr"]
    assert page["status"] == "needs_review"
    assert candidate["status"] == "needs_review"
    assert candidate["text"] == OCR_TEXT
    assert candidate["blocks"] == [OCR_BLOCK]
    assert pending["coverage"]["ocr_pending_pages"] == 1
    assert pending["pages"] == []
    assert build_chunks(pending, "recursive") == []

    review_path = tmp_path / "review.json"
    wrong_sha = "0" * 64 if candidate["candidate_sha256"] != "0" * 64 else "1" * 64
    write_sidecar(review_path, source.name, {"ocr_reviews": {"1": wrong_sha}})
    mismatch = read_dataset(data, ingestor.ingest(source, review_path, ocr=True))
    assert mismatch["raw_pages"][0]["ocr"]["candidate_sha256"] == candidate["candidate_sha256"]
    assert mismatch["pages"] == []
    assert any("checksum does not match" in item["reason"] for item in mismatch["coverage"]["issues"])

    write_sidecar(review_path, source.name, {"ocr_reviews": {"1": candidate["candidate_sha256"]}})
    approved = read_dataset(data, ingestor.ingest(source, review_path, ocr=True))
    approved_page, = approved["pages"]
    assert approved_page["status"] == "passed"
    assert approved_page["review_status"] == "verified"
    assert approved_page["extraction_method"] == "ocr_reviewed"
    assert approved_page["clean_text"] == OCR_CLEAN_TEXT
    assert approved_page["raw_text"] == ""
    assert approved_page["pdf_page"] == page["pdf_page"] == 1
    assert approved_page["evidence_id"] == page["evidence_id"]
    assert approved_page["ocr"] == {**candidate, "status": "approved", "approval_scope": "whole_page"}
    assert approved["coverage"]["ocr_approved_pages"] == 1
    assert len({ds["dataset_id"] for ds in (pending, mismatch, approved)}) == 3
    assert ocr_calls == [(0, 300)]
    assert digest(source) == original_sha
    document, = approved["documents"]
    assert digest(data / document["reference_path"]) == original_sha
    chunk, = build_chunks(approved, "recursive")
    assert chunk["text"] == OCR_CLEAN_TEXT
    span, = chunk["source_spans"]
    assert span["pdf_page"] == 1 and span["evidence_id"] == page["evidence_id"]


@pytest.mark.parametrize(("excerpt", "eligible"), [
    (f"  {OCR_EXCERPT}", True),
    ("7.48.02.01 (IE3)", False),
    ("Chuẩn đầu ra ngành Công nghệ thông tin.\nThời gian đào tạo: chưa đối chiếu.", False),
])
def test_ocr_excerpt_requires_contiguous_complete_lines(tmp_path, ocr_calls, excerpt, eligible):
    source, data = tmp_path / "scan.pdf", tmp_path / "data"
    make_pdf(source, scan=True)
    ingestor = Ingestor(data)
    pending = read_dataset(data, ingestor.ingest(source, ocr=True))
    checksum = pending["raw_pages"][0]["ocr"]["candidate_sha256"]
    sidecar = write_sidecar(tmp_path / "review.json", source.name, {
        "ocr_reviews": {"1": {"candidate_sha256": checksum, "excerpt": excerpt}},
    })
    dataset = read_dataset(data, ingestor.ingest(source, sidecar, ocr=True))
    page, = dataset["raw_pages"]
    assert page["ocr"]["text"] == OCR_TEXT
    assert ocr_calls == [(0, 300)]
    if not eligible:
        assert dataset["pages"] == []
        assert dataset["coverage"]["ocr_pending_pages"] == 1
        assert any("complete OCR lines" in item["reason"] for item in dataset["coverage"]["issues"])
        return
    assert page["ocr"]["approval_scope"] == "excerpt"
    assert dataset["documents"][0]["status"] == "needs_review"
    assert dataset["coverage"]["ocr_approved_excerpt_pages"] == 1
    for strategy in ("recursive", "fixed"):
        chunk, = build_chunks(dataset, strategy)
        assert chunk["text"] == OCR_EXCERPT
        assert chunk["source_spans"][0]["evidence_id"] == page["evidence_id"]


def test_ocr_bbox_change_invalidates_candidate_approval(tmp_path, ocr_calls):
    source, data = tmp_path / "scan.pdf", tmp_path / "data"
    make_pdf(source, scan=True)
    ingestor = Ingestor(data)
    pending = read_dataset(data, ingestor.ingest(source, ocr=True))
    checksum = pending["raw_pages"][0]["ocr"]["candidate_sha256"]
    cache_path, = (data / "staging" / "ocr").glob("*.json")
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache["result"]["blocks"][0]["bbox"][0] += 1.0
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    sidecar = write_sidecar(tmp_path / "review.json", source.name, {"ocr_reviews": {"1": checksum}})
    dataset = read_dataset(data, ingestor.ingest(source, sidecar, ocr=True))
    candidate = dataset["raw_pages"][0]["ocr"]
    assert candidate["text"] == OCR_TEXT
    assert candidate["candidate_sha256"] != checksum
    assert dataset["pages"] == []
    assert any("checksum does not match" in item["reason"] for item in dataset["coverage"]["issues"])
    assert ocr_calls == [(0, 300)]


def test_default_ingestion_retains_text_only_behavior(tmp_path, monkeypatch):
    source, data = tmp_path / "source", tmp_path / "data"
    make_pdf(source / "scan.pdf", scan=True)
    make_pdf(source / "digital.pdf")

    def unexpected_engine(*args):
        pytest.fail("Default ingestion must not require or start the OCR engine")

    monkeypatch.setattr(ocr, "OCREngine", unexpected_engine)
    dataset = read_dataset(data, Ingestor(data).ingest(source))
    assert dataset["pipeline_revision"] == "pdf-pages-v1"
    assert "ocr_contract" not in dataset
    assert len(dataset["pages"]) == 1
    assert "approved Word source" in dataset["pages"][0]["clean_text"]
    scan_page, = [page for page in dataset["raw_pages"] if page["classification"] == "image"]
    assert scan_page["status"] == "needs_review" and "ocr" not in scan_page


@pytest.fixture
def word_reference(tmp_path):
    source, data = tmp_path / "word.docx", tmp_path / "data"
    source.write_bytes(b"Opaque original Word source bytes")
    candidate = data / "staging" / "word" / "reference.pdf"
    make_pdf(candidate)
    reference = {"path": candidate.relative_to(data).as_posix(), "source_sha256": digest(source),
                 "sha256": digest(candidate), "reviewed_pages": []}
    return source, data, candidate, reference


def test_word_reference_only_uses_reviewed_pages(tmp_path, word_reference):
    source, data, candidate, reference = word_reference
    path = write_sidecar(tmp_path / "review.json", source.name, {"reference_pdf": reference})
    ingestor = Ingestor(data)
    pending = read_dataset(data, ingestor.ingest(source, path))
    assert pending["pages"] == []
    assert build_chunks(pending, "recursive") == []
    assert pending["raw_pages"][0]["status"] == "needs_review"
    assert any("page mapping requires review" in item["reason"] for item in pending["coverage"]["issues"])

    reference["reviewed_pages"] = [1]
    write_sidecar(path, source.name, {"reference_pdf": reference})
    approved = read_dataset(data, ingestor.ingest(source, path))
    page, = approved["pages"]
    document, = approved["documents"]
    snapshot = data / document["snapshot_path"]
    pdf_snapshot = data / document["reference_path"]
    assert page["status"] == "passed" and page["pdf_page"] == 1
    assert document["reference_sha256"] == digest(pdf_snapshot) == reference["sha256"]
    assert document["sha256"] == digest(snapshot) == reference["source_sha256"]
    assert pdf_snapshot != candidate and pdf_snapshot != snapshot
    assert source_checksums(approved) == {
        document["snapshot_path"]: reference["source_sha256"],
        document["reference_path"]: reference["sha256"],
    }
    chunk, = build_chunks(approved, "recursive")
    assert chunk["source_spans"][0]["pdf_page"] == 1


@pytest.mark.parametrize(("field", "reason"), [
    ("sha256", "PDF checksum mismatch"),
    ("source_sha256", "matching Word snapshot SHA-256"),
])
def test_word_reference_rejects_invalid_digests(tmp_path, word_reference, field, reason):
    source, data, _, reference = word_reference
    reference.update(reviewed_pages=[1], **{field: "0" * 64})
    path = write_sidecar(tmp_path / "review.json", source.name, {"reference_pdf": reference})
    dataset = read_dataset(data, Ingestor(data).ingest(source, path))
    assert dataset["pages"] == []
    assert dataset["documents"][0]["reference_path"] is None
    assert reason in dataset["documents"][0]["blocked_reason"]
