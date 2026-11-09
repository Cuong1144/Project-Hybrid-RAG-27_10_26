"""Local PDF page OCR using MuPDF's embedded Tesseract engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.request import urlopen

import pymupdf

from .storage import digest


DEFAULT_TESSDATA = Path(__file__).resolve().parents[1] / ".cache" / "tessdata"
DEFAULT_DPI = 300
MAX_PAGE_PIXELS = 32_000_000
LANGUAGE = "vie+eng"
ENGINE = "pymupdf-tesseract"
COORDINATE_SYSTEM = "pdf_points_top_left_unrotated"
TESSDATA_REVISION = "65727574dfcd264acbb0c3e07860e4e9e9b22185"  # tessdata_fast 4.1.0
MODEL_SHA256 = {
    "vie.traineddata": "79df64caf7bcfb2a27df5042ecb6121e196eada34da774956995747636d5bfa1",
    "eng.traineddata": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}


class OCREngine:
    """Recognize a rendered page and keep block bounds in original PDF points."""

    def __init__(self, tessdata: Path = DEFAULT_TESSDATA):
        self.tessdata = Path(tessdata).resolve()
        for filename, expected in MODEL_SHA256.items():
            path = self.tessdata / filename
            if not path.is_file() or digest(path) != expected:
                raise ValueError(
                    f"OCR model missing or checksum mismatch: {path}. "
                    "Run python -m utc_rag.ocr install to install the pinned language data."
                )

    @property
    def contract(self) -> dict:
        return {
            "revision": "pdf-page-ocr-v1",
            "engine": ENGINE,
            "pymupdf_version": pymupdf.VersionBind,
            "mupdf_version": pymupdf.VersionFitz,
            "language": LANGUAGE,
            "render_mode": "full_page",
            "tessdata_revision": TESSDATA_REVISION,
            "model_sha256": dict(MODEL_SHA256),
            "coordinate_system": COORDINATE_SYSTEM,
            "max_page_pixels": MAX_PAGE_PIXELS,
        }

    def recognize(self, page: pymupdf.Page, *, dpi: int = DEFAULT_DPI) -> dict:
        if type(dpi) is not int or not 72 <= dpi <= 600:
            raise ValueError("OCR DPI must be an integer between 72 and 600.")
        rect = page.rect
        if rect.is_empty or rect.is_infinite:
            raise ValueError("OCR page has invalid bounds.")
        width_px = math.ceil(rect.width * dpi / 72)
        height_px = math.ceil(rect.height * dpi / 72)
        if width_px * height_px > MAX_PAGE_PIXELS:
            raise ValueError("OCR render exceeds the per-page pixel budget. Reduce the DPI.")

        # Extract before transforming. Extracting through get_textpage_ocr on a
        # rotated page can clip the mapped text against the raster page's bounds.
        pixmap = page.get_pixmap(dpi=dpi)
        layer = pixmap.pdfocr_tobytes(compress=False, language=LANGUAGE, tessdata=str(self.tessdata))
        with pymupdf.open("pdf", layer) as temporary_pdf:
            ocr_page = temporary_pdf[0]
            to_source = ocr_page.rect.torect(rect) * page.derotation_matrix
            recognized = ocr_page.get_text("blocks", flags=pymupdf.TEXTFLAGS_BLOCKS & ~pymupdf.TEXT_PRESERVE_IMAGES,
                                           sort=True)
        page_bounds = rect * page.derotation_matrix
        blocks = []
        discarded = 0
        for x0, y0, x1, y1, block_text, block_number, block_type in recognized:
            if block_type != 0 or not block_text.strip():
                continue
            bbox = (pymupdf.Rect(x0, y0, x1, y1) * to_source) & page_bounds
            if not all(math.isfinite(value) for value in bbox):
                raise ValueError("OCR returned a text block without valid PDF coordinates.")
            if bbox.is_empty:
                discarded += 1
                continue
            blocks.append({"bbox": list(bbox), "text": block_text.rstrip("\r\n"),
                           "block_number": block_number, "block_type": block_type})
        text = "\n\n".join(block["text"] for block in blocks)
        quality = {"character_count": len(text), "replacement_character_count": text.count("\ufffd"),
                   "block_count": len(blocks), "table_structure_verified": False}
        if discarded:
            quality["discarded_off_page_blocks"] = discarded
        return {
            "engine": ENGINE,
            "language": LANGUAGE,
            "dpi": dpi,
            "text": text,
            "blocks": blocks,
            "bbox": list(page_bounds),
            "coordinate_system": COORDINATE_SYSTEM,
            "confidence": None,  # MuPDF's TextPage does not expose Tesseract scores.
            "quality": quality,
        }


def install_models(tessdata: Path = DEFAULT_TESSDATA) -> dict:
    """Install only the two pinned official model files and verify their hashes."""
    tessdata = Path(tessdata).resolve()
    tessdata.mkdir(parents=True, exist_ok=True)
    for filename, expected in MODEL_SHA256.items():
        target = tessdata / filename
        if target.is_file() and digest(target) == expected:
            continue
        url = f"https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/{TESSDATA_REVISION}/{filename}"
        with urlopen(url, timeout=45) as response:
            content = response.read(8 * 1024 * 1024 + 1)
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError(f"Downloaded OCR model checksum mismatch: {filename}")
        with tempfile.NamedTemporaryFile(dir=tessdata, delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(content)
                output.close()
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    return OCREngine(tessdata).contract


def main() -> None:
    parser = argparse.ArgumentParser(description="Install or check the local Vietnamese/English OCR models.")
    parser.add_argument("command", choices=("install", "check"))
    parser.add_argument("--tessdata", type=Path, default=DEFAULT_TESSDATA)
    args = parser.parse_args()
    try:
        contract = install_models(args.tessdata) if args.command == "install" else OCREngine(args.tessdata).contract
    except (OSError, ValueError) as error:
        parser.exit(1, f"OCR: {error}\n")
    print(json.dumps(contract, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
