"""Export staged text and checksums so a reviewer can compare with source PDFs."""
from pathlib import Path

from .storage import write_json


def export_review(dataset: dict, data_dir: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    pages_by_file = {}
    for page in dataset["raw_pages"]:
        pages_by_file.setdefault(page["file_id"], []).append(page)
    coverage = dataset["coverage"]
    summary = {"dataset_id": dataset["dataset_id"], "coverage": coverage, "files": []}
    lines = ["# Kết quả nhận dạng tài liệu UTC", "",
             f"Dataset `{dataset['dataset_id']}`", "",
             f"OCR đã chạy trên {coverage.get('ocr_attempted_pages', 0)} trang. "
             f"Có {coverage.get('ocr_pending_pages', 0)} trang chờ đối chiếu, "
             f"{coverage.get('ocr_approved_pages', 0)} trang đã đối chiếu và "
             f"{coverage.get('ocr_empty_pages', 0)} trang không nhận được chữ và "
             f"{coverage.get('ocr_failed_pages', 0)} trang gặp lỗi.", "",
             "Mở bản văn bản và đối chiếu trang PDF gốc trước khi duyệt. "
             "Bảng, mã học phần, số văn bản và ngày tháng cần kiểm tra trực quan.", ""]
    for document in dataset["documents"]:
        pages = [p for p in pages_by_file.get(document["file_id"], [])
                 if "ocr" in p or document.get("reference_sha256")]
        if not pages:
            continue
        name = f"source-{len(summary['files']) + 1:03d}.txt"
        reference = data_dir / document["reference_path"]
        file_report = {"file_id": document["file_id"], "relative_path": document["relative_path"],
                       "reference_pdf": str(reference), "text_file": name, "pages": []}
        content = [document["relative_path"], f"PDF tham chiếu: {reference}", ""]
        for page in pages:
            ocr = page.get("ocr", {})
            state = ocr.get("status", page["status"])
            checksum = ocr.get("candidate_sha256")
            text = ocr.get("text", page["clean_text"])
            file_report["pages"].append({"pdf_page": page["pdf_page"], "status": state,
                                         "candidate_sha256": checksum, "characters": len(text),
                                         "quality": ocr.get("quality"), "error": ocr.get("error")})
            content.extend([f"--- Trang PDF {page['pdf_page']} · {state} ---",
                            f"SHA ứng viên: {checksum}" if checksum else "", text or ocr.get("error", ""), ""])
        (output / name).write_text("\n".join(content), encoding="utf-8")
        summary["files"].append(file_report)
        title = document["relative_path"].replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- {title} · {len(pages)} trang · [Văn bản trích xuất]({name})")
    lines.extend(["", "Thông tin trang, mã băm ứng viên và đường dẫn PDF nằm trong [danh mục rà soát](review.json).", ""])
    (output / "index.md").write_text("\n".join(lines), encoding="utf-8")
    write_json(output / "review.json", summary)
    return {"output": str(output), "files": len(summary["files"]), "ocr_attempted_pages": coverage.get("ocr_attempted_pages", 0)}
