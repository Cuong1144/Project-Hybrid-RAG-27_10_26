from pathlib import Path

import pymupdf
import pytest

from utc_rag.ocr import COORDINATE_SYSTEM, DEFAULT_TESSDATA, MODEL_SHA256, OCREngine


@pytest.fixture(scope="module")
def engine():
    if not all((DEFAULT_TESSDATA / name).is_file() for name in MODEL_SHA256):
        pytest.skip("Install the pinned OCR models with python -m utc_rag.ocr install")
    return OCREngine()


def test_missing_or_wrong_language_data_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="OCR model missing or checksum mismatch"):
        OCREngine(tmp_path)
    for name in MODEL_SHA256:
        (tmp_path / name).write_bytes(b"not a language model")
    with pytest.raises(ValueError, match="checksum mismatch"):
        OCREngine(tmp_path)


def test_render_budget_rejects_oversized_page_before_ocr(engine):
    with pymupdf.open() as document:
        page = document.new_page(width=2400, height=2400)
        with pytest.raises(ValueError, match="pixel budget"):
            engine.recognize(page, dpi=300)


def test_rotated_scan_preserves_full_text_and_source_coordinates(engine):
    label = "TRANSPORT UNIVERSITY"
    with pymupdf.open() as original, pymupdf.open() as scanned:
        page = original.new_page(width=390, height=240)
        page.insert_text((45, 100), label, fontsize=22)
        expected = page.search_for(label)[0]
        source = scanned.new_page(width=240, height=390)
        source.insert_image(source.rect, pixmap=page.get_pixmap(dpi=180), rotate=90)
        source.set_rotation(90)
        assert not source.get_text().strip()

        recognized = engine.recognize(source, dpi=180)

        assert recognized["text"] == label
        assert recognized["coordinate_system"] == COORDINATE_SYSTEM
        assert recognized["confidence"] is None
        assert source.rotation == 90
        assert not source.get_text().strip()
        assert recognized["bbox"] == [0, 0, 240, 390]
        mapped = pymupdf.Rect(recognized["blocks"][0]["bbox"]) * source.rotation_matrix
        assert abs(mapped.x0 - expected.x0) < 5
        assert abs(mapped.x1 - expected.x1) < 5
        assert expected.contains(mapped)


def test_read_real_vietnamese_scan_when_source_available(engine):
    corpus = Path(__file__).resolve().parents[2] / "Tailieu"
    source = next(corpus.rglob("clc-cntt.pdf"), None)
    if source is None:
        pytest.skip("UTC scanned PDF fixture is not present")
    with pymupdf.open(source) as document:
        page = document[0]
        assert not page.get_text().strip()
        result = engine.recognize(page)
    assert "TRƯỜNG ĐẠI HỌC GIAO THÔNG VẬN TẢI" in result["text"]
    assert result["language"] == "vie+eng"
    assert result["blocks"]
    assert all(pymupdf.Rect(result["bbox"]).contains(pymupdf.Rect(item["bbox"])) for item in result["blocks"])
    assert result["quality"]["table_structure_verified"] is False
