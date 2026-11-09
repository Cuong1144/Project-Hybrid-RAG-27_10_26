"""Snapshot sources and stage PDF text or OCR with page-level review."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from datetime import datetime, timezone
import unicodedata
import uuid

import pymupdf

from .storage import digest as _hash_file, write_json as _write_json


PIPELINE_REVISION = "pdf-pages-v1"
VERIFICATION_STATES = {
    "verified", "unverified", "conflicting", "not_found", "not_applicable"
}
DEFAULT_METADATA_FIELDS = (
    "major_code", "major_name", "degree_level", "program_type",
    "program_version_id", "cohort_from", "cohort_to", "document_type",
    "number_symbol", "issue_date", "effective_date", "issuing_authority",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _clean_text(raw: str) -> str:
    # Keep line and paragraph boundaries: conditions and table rows are not
    # merged into newly invented prose.
    text = unicodedata.normalize("NFC", raw).replace("\x00", "")
    return "\n".join(re.sub(r"[\t \u00a0]+", " ", line).rstrip()
                     for line in text.splitlines()).strip()


def _normalized(text: object) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text)).casefold().split())


def _ocr_review_value(value):
    if isinstance(value, str):
        value = {"candidate_sha256": value}
    if not isinstance(value, dict) or set(value) - {"candidate_sha256", "excerpt"}:
        raise ValueError("OCR review must contain a candidate SHA-256 and optional exact excerpt")
    checksum, excerpt = value.get("candidate_sha256"), value.get("excerpt")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid OCR candidate SHA-256")
    if excerpt is not None and (not isinstance(excerpt, str) or not excerpt.strip()):
        raise ValueError("OCR excerpt must contain complete source lines")
    return checksum, excerpt


class Ingestor:
    """Build a deterministic dataset from a local source tree and review file."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def ingest(self, source_dir: Path, metadata_path: Path | None = None,
               *, ocr: bool = False, expand_archives: bool = False) -> dict:
        source_dir = Path(source_dir).resolve()
        if not source_dir.exists():
            raise ValueError("Source path does not exist")
        if source_dir.is_dir() and self.data_dir.is_relative_to(source_dir):
            raise ValueError("Source directory must not contain the data directory")
        sidecar = {}
        if metadata_path:
            sidecar = json.loads(Path(metadata_path).read_text(encoding="utf-8-sig"))
            if not isinstance(sidecar, dict) or not isinstance(sidecar.get("files", {}), dict):
                raise ValueError("Metadata must be an object with a files object")
        engine = None
        if ocr:
            from .ocr import DEFAULT_TESSDATA, OCREngine
            engine = OCREngine(DEFAULT_TESSDATA)
        files = sorted([source_dir] if source_dir.is_file() else
                       [p for p in source_dir.rglob("*") if p.is_file()],
                       key=lambda p: p.as_posix().casefold())
        inventory = []
        for path in files:
            relative = path.name if source_dir.is_file() else path.relative_to(source_dir).as_posix()
            record = {"relative_path": relative, "path": path}
            try:
                if path.is_symlink():
                    raise ValueError("Symbolic links are excluded from source ingestion")
                record.update(sha256=_hash_file(path), size_bytes=path.stat().st_size)
            except (OSError, ValueError) as exc:
                record["error"] = str(exc)
            inventory.append(record)
        archive_stages = self._expand_archives(inventory) if expand_archives else {}
        has_references = any(isinstance(v, dict) and "reference_pdf" in v for v in sidecar.get("files", {}).values())
        revision = "pdf-ocr-v3" if ocr or expand_archives or has_references else PIPELINE_REVISION
        identity = {
            "pipeline_revision": revision,
            "inventory": [{k: v for k, v in row.items() if k != "path"} for row in inventory],
            "metadata": sidecar,
        }
        if engine is not None:
            identity["ocr_contract"] = engine.contract
        if expand_archives:
            identity["archive_stages"] = archive_stages
        signature = _digest(identity)
        dataset_id = "dataset_" + signature[:24]
        job_id = "ingest_" + signature[:24]
        job_path = self.data_dir / "staging" / "jobs" / (job_id + ".json")
        dataset_path = self.data_dir / "datasets" / dataset_id / "dataset.json"
        if job_path.exists() and dataset_path.exists():
            existing = json.loads(dataset_path.read_text(encoding="utf-8"))
            # A resumed job may only reuse content that still matches its
            # registry.  File existence alone does not establish a checkpoint.
            for document in existing["documents"]:
                snapshot = self.data_dir / document["snapshot_path"]
                if not snapshot.is_file() or _hash_file(snapshot) != document["sha256"]:
                    raise ValueError("Existing dataset snapshot checksum mismatch")
                if document.get("reference_path") and document.get("reference_sha256") and _hash_file(self.data_dir / document["reference_path"]) != document["reference_sha256"]:
                    raise ValueError("Existing dataset reference PDF checksum mismatch")
            job = json.loads(job_path.read_text(encoding="utf-8"))
            job["reused"] = True
            return job
        created_at = _now()
        job = {
            "job_id": job_id, "run_id": job_id, "status": "running",
            "dataset_id": dataset_id, "dataset_version": dataset_id,
            "source_path": str(source_dir), "created_at": created_at,
            "pipeline_revision": revision,
        }
        _write_json(job_path, job)
        dataset = {
            "dataset_id": dataset_id, "dataset_version": dataset_id,
            "created_at": created_at, "pipeline_revision": revision,
            "documents": [], "pages": [], "raw_pages": [], "page_inventory": [],
            "source_occurrences": [], "coverage": {
                "total_files": len(inventory), "eligible_files": 0,
                "blocked_files": 0, "duplicate_files": 0, "issues": [],
                "total_pages": 0, "eligible_pages": 0, "blocked_pages": 0,
            },
            "limitations": [
                "PDF text extraction baseline; curriculum table completeness and selection rules are not verified.",
                "OCR and Word reference pages require separate review before publication.",
                "Unknown metadata is not inferred from filenames or directory years.",
            ],
        }
        if engine is not None:
            dataset["ocr_contract"] = engine.contract
        if expand_archives:
            dataset["archive_stages"] = archive_stages
            dataset["coverage"]["original_files"] = len(files)
            dataset["coverage"]["archive_member_files"] = len(inventory) - len(files)
            dataset["coverage"]["expanded_archives"] = 0
        documents_by_hash = {}
        for row in inventory:
            relative = row["relative_path"]
            occurrence = {"relative_path": relative, "source_root": str(source_dir),
                          "received_at": created_at}
            dataset["source_occurrences"].append(occurrence)
            if "error" in row:
                occurrence.update(status="blocked", reason=row["error"])
                self._issue(dataset, relative, "P1-01", row["error"], "blocking")
                dataset["coverage"]["blocked_files"] += 1
                continue
            digest = row["sha256"]
            file_id = "file_" + digest[:24]
            occurrence.update(file_id=file_id, sha256=digest, size_bytes=row["size_bytes"])
            if "archive_origin" in row:
                occurrence["archive_origin"] = row["archive_origin"]
            if digest in documents_by_hash:
                occurrence.update(status="duplicate_linked", canonical_file_id=file_id)
                dataset["coverage"]["duplicate_files"] += 1
                continue
            source = row["path"]
            try:
                snapshot = self._snapshot(source, digest)
            except (OSError, ValueError) as exc:
                occurrence.update(status="blocked", reason=str(exc))
                self._issue(dataset, relative, "P1-02", str(exc), "blocking")
                dataset["coverage"]["blocked_files"] += 1
                continue
            review = sidecar.get("files", {}).get(relative, {})
            if not isinstance(review, dict):
                raise ValueError("Metadata file entry must be an object: " + relative)
            display_name = row.get("display_name", source.name)
            document = {
                "file_id": file_id, "sha256": digest,
                "original_name": display_name, "relative_path": relative,
                "title": review.get("title") or Path(display_name).stem,
                "snapshot_path": snapshot.relative_to(self.data_dir).as_posix(),
                "reference_path": None, "page_count": 0,
                # A physical file does not establish a logical-document or
                # program identity.  These fields remain unknown by default.
                "logical_document_id": review.get("logical_document_id"),
                "document_version_id": review.get("document_version_id"),
                "metadata": {}, "status": "blocked",
                "mime_type": "application/octet-stream",
            }
            if "archive_origin" in row:
                document["archive_origin"] = row["archive_origin"]
            dataset["documents"].append(document)
            documents_by_hash[digest] = document
            extension = source.suffix.casefold()
            archive = archive_stages.get(relative)
            if archive and any(m["status"] == "staged" for m in archive.get("members", [])):
                document["status"] = occurrence["status"] = "expanded"
                dataset["coverage"]["expanded_archives"] += 1
                continue
            word_reference = extension in {".doc", ".docx"} and "reference_pdf" in review
            if extension != ".pdf" and not word_reference:
                reason = {
                    ".doc": "Legacy Word requires conversion and verified PDF page mapping",
                    ".docx": "Word requires a fixed-layout PDF and verified page mapping",
                    ".rar": "RAR requires safe entry enumeration, extraction and per-entry review",
                    ".zip": "Archive requires safe entry enumeration, extraction and per-entry review",
                }.get(extension, "Unsupported source format: " + (extension or "no extension"))
                if archive and archive.get("error"):
                    reason = archive["error"]
                document["blocked_reason"] = reason
                occurrence.update(status="blocked", reason=reason)
                self._issue(dataset, relative, "P1-03", reason, "blocking", file_id=file_id)
                dataset["coverage"]["blocked_files"] += 1
                continue
            try:
                reference = self._word_reference(document, review) if word_reference else snapshot
                with pymupdf.open(reference) as pdf:
                    if pdf.needs_pass:
                        raise ValueError("Encrypted PDF requires a readable reviewed source")
                    if not pdf.is_pdf or pdf.page_count < 1:
                        raise ValueError("Invalid or empty PDF")
                    document.update(reference_path=reference.relative_to(self.data_dir).as_posix(),
                                    page_count=pdf.page_count, mime_type="application/pdf")
                    raw_pages = self._extract_pages(pdf, document, review, dataset, engine)
                document["metadata"] = self._metadata(
                    review.get("fields", {}), raw_pages, dataset, relative, file_id)
                for page in raw_pages:
                    page["metadata"] = dict(document["metadata"])
                    override = review.get("page_overrides", {}).get(str(page["pdf_page"]), {})
                    if override:
                        fields = override.get("fields", override)
                        if not isinstance(fields, dict):
                            raise ValueError("Page metadata override must be an object")
                        page["metadata"].update(self._metadata(
                            fields, raw_pages, dataset, relative, file_id,
                            defaults=False))
                eligible_pages = [p for p in raw_pages if p["status"] == "passed" and p["clean_text"]]
                dataset["pages"].extend(eligible_pages)
                eligible = len(eligible_pages)
                document["eligible_page_count"] = eligible
                whole_pages = not any(p.get("ocr", {}).get("approval_scope") == "excerpt" for p in eligible_pages)
                document["status"] = "passed" if eligible == document["page_count"] and whole_pages else (
                    "needs_review" if eligible else "blocked")
                occurrence["status"] = document["status"]
                if eligible:
                    dataset["coverage"]["eligible_files"] += 1
                else:
                    dataset["coverage"]["blocked_files"] += 1
            except Exception as exc:
                # One malformed file must not erase independently valid files.
                document.update(status="blocked", blocked_reason=str(exc))
                occurrence.update(status="blocked", reason=str(exc))
                self._issue(dataset, relative, "P1-05", str(exc), "blocking", file_id=file_id)
                dataset["coverage"]["blocked_files"] += 1
        coverage = dataset["coverage"]
        coverage["eligible_pages"] = len(dataset["pages"])
        coverage["blocked_pages"] = sum(p["status"] != "passed" for p in dataset["page_inventory"])
        coverage["total_pages"] = len(dataset["page_inventory"])
        coverage["unique_files"] = len(dataset["documents"])
        coverage["incomplete_files"] = sum(d["status"] == "needs_review" for d in dataset["documents"])
        if engine is not None:
            outcomes = [p["ocr"]["status"] for p in dataset["raw_pages"] if "ocr" in p]
            coverage.update(ocr_attempted_pages=len(outcomes), ocr_pending_pages=outcomes.count("needs_review"),
                            ocr_approved_pages=outcomes.count("approved"), ocr_failed_pages=outcomes.count("failed"),
                            ocr_empty_pages=outcomes.count("empty"))
            coverage["ocr_approved_excerpt_pages"] = sum(p.get("ocr", {}).get("approval_scope") == "excerpt" for p in dataset["pages"])
        job.update(status="completed_with_issues" if coverage["issues"] else "completed",
                   finished_at=_now(), coverage=coverage,
                   counts={"files": coverage["total_files"], "documents": len(dataset["documents"]),
                           "pages": len(dataset["pages"]), "blocked": coverage["blocked_files"]},
                   dataset_path=dataset_path.relative_to(self.data_dir).as_posix())
        _write_json(dataset_path, dataset)
        _write_json(job_path, job)
        return job

    def _snapshot(self, source: Path, sha256: str) -> Path:
        snapshot = self.data_dir / "snapshots" / sha256 / source.name
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            temporary = snapshot.with_name(snapshot.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                shutil.copyfile(source, temporary)
                if _hash_file(temporary) != sha256:
                    raise ValueError("Source changed while snapshot was copied")
                os.replace(temporary, snapshot)
            finally:
                temporary.unlink(missing_ok=True)
        if _hash_file(snapshot) != sha256:
            raise ValueError("Snapshot checksum mismatch")
        return snapshot

    def _word_reference(self, document: dict, review: dict) -> Path:
        reference = review["reference_pdf"]
        if not isinstance(reference, dict) or reference.get("source_sha256") != document["sha256"]:
            raise ValueError("Reference PDF must identify the matching Word snapshot SHA-256")
        candidate = (self.data_dir / reference["path"]).resolve()
        if not candidate.is_relative_to(self.data_dir / "staging") or candidate.suffix.casefold() != ".pdf":
            raise ValueError("Word reference PDF must be under data/staging")
        sha256 = _hash_file(candidate)
        if sha256 != reference.get("sha256"):
            raise ValueError("Word reference PDF checksum mismatch")
        document["reference_sha256"] = sha256
        document["reference_name"] = Path(document["original_name"]).stem + ".pdf"
        return self._snapshot(candidate, sha256)

    def _expand_archives(self, inventory: list[dict]) -> dict:
        from .archives import ArchiveError, unpack_rar

        stages, children, expanded = {}, [], set()
        for row in inventory:
            if "error" in row or row["path"].suffix.casefold() != ".rar" or row["sha256"] in expanded:
                continue
            sha256 = row["sha256"]
            expanded.add(sha256)
            relative = row["relative_path"]
            try:
                snapshot = self._snapshot(row["path"], sha256)
                result = unpack_rar(snapshot, self.data_dir / "staging" / "archives" / sha256)
                if result["archive_sha256"] != sha256:
                    raise ValueError("Archive snapshot checksum mismatch")
                records = []
                for member in result["members"]:
                    entry = {k: v for k, v in member.items() if k != "path"}
                    if member["status"] == "staged":
                        path = Path(member["path"]).resolve()
                        if not path.is_relative_to(self.data_dir / "staging" / "archives" / sha256):
                            raise ValueError("Extracted member outside its staging directory")
                        entry["staged_path"] = path.relative_to(self.data_dir).as_posix()
                        children.append({"relative_path": relative + "::" + member["name"],
                                         "display_name": Path(member["name"]).name, "path": path,
                                         "sha256": member["sha256"], "size_bytes": member["size"],
                                         "archive_origin": {"relative_path": relative, "entry_name": member["name"],
                                                            "sha256": sha256, "snapshot_path": snapshot.relative_to(self.data_dir).as_posix()}})
                    records.append(entry)
                stages[relative] = {"sha256": sha256, "members": records}
            except (ArchiveError, OSError, ValueError) as exc:
                stages[relative] = {"sha256": sha256, "error": str(exc)}
        inventory.extend(children)
        return stages

    @staticmethod
    def _issue(dataset: dict, relative: str, step: str, reason: str,
               severity: str = "warning", **location) -> None:
        dataset["coverage"]["issues"].append({
            "relative_path": relative, "step": step, "reason": reason,
            "severity": severity, "status": "needs_review", **location,
        })

    def _extract_pages(self, pdf, document: dict, review: dict,
                       dataset: dict, engine=None) -> list[dict]:
        pages = []
        reviewed = review.get("reviewed_pages", [])
        if not isinstance(reviewed, list) or any(type(p) is not int or p < 1 or p > pdf.page_count for p in reviewed):
            raise ValueError("reviewed_pages must contain valid 1-based page numbers")
        reviewed = set(reviewed)
        ocr_reviews = review.get("ocr_reviews", {})
        if not isinstance(ocr_reviews, dict) or any(
            not isinstance(page, str) or not page.isdecimal() or not 1 <= int(page) <= pdf.page_count
            for page in ocr_reviews
        ):
            raise ValueError("ocr_reviews must map valid page numbers to candidate SHA-256 hashes")
        ocr_reviews = {page: _ocr_review_value(value) for page, value in ocr_reviews.items()}
        reference_reviewed = None
        if "reference_sha256" in document:
            reference_reviewed = review["reference_pdf"].get("reviewed_pages", [])
            if not isinstance(reference_reviewed, list) or any(type(p) is not int or not 1 <= p <= pdf.page_count for p in reference_reviewed):
                raise ValueError("Invalid reviewed page mapping for Word reference PDF")
            reference_reviewed = set(reference_reviewed)
        for page_index in range(pdf.page_count):
            source_page = pdf[page_index]
            number = page_index + 1
            raw = source_page.get_text("text", sort=True)
            clean = _clean_text(raw)
            blocks = [
                {"bbox": list(block[:4]), "text": block[4],
                 "block_number": block[5], "block_type": block[6]}
                for block in source_page.get_text("blocks", sort=True)
            ]
            image_area = 0.0
            page_bounds = source_page.rect * source_page.derotation_matrix
            for info in source_page.get_image_info():
                box = pymupdf.Rect(info["bbox"]) & page_bounds
                image_area += max(0.0, box.get_area())
            image_fraction = min(1.0, image_area / max(1, source_page.rect.get_area()))
            reason = None
            classification = "text"
            if not clean:
                needs_ocr = bool(image_fraction or source_page.get_drawings())
                classification = "image" if image_fraction else ("vector_content" if needs_ocr else "blank_unverified")
                reason = ("Page needs OCR and source review" if needs_ocr
                          else "Blank page needs explicit review before exclusion")
            elif "\ufffd" in clean and clean.count("\ufffd") / len(clean) > 0.005:
                classification, reason = "damaged_text", "Text contains replacement characters; inspect extraction"
            elif image_fraction > 0.65 or (image_fraction > 0.25 and len(clean) < 160):
                classification, reason = "mixed", "Mixed image/text page needs region or OCR review"
            status = "needs_review" if reason else "passed"
            if number in reviewed:
                # Manual approval can accept an actually blank page, but cannot
                # fabricate text for an image page without an OCR derivative.
                if clean or classification == "blank_unverified":
                    status = "passed"
                    reason = None
            ocr_result = None
            review_status = "verified" if number in reviewed and status == "passed" else "unverified"
            extraction_method = "pdf_text"
            issue_step = "P1-04"
            if reason and engine is not None and classification != "blank_unverified":
                issue_step = "P1-06"
                try:
                    ocr_result = self._ocr_candidate(engine, source_page, document, number)
                    candidate = _clean_text(ocr_result["text"])
                    approved_checksum, excerpt = ocr_reviews.get(str(number), (None, None))
                    if not candidate:
                        ocr_result["status"] = "empty"
                        reason = "OCR returned no usable text"
                    elif approved_checksum == ocr_result["candidate_sha256"]:
                        reviewed_text = _clean_text(excerpt) if excerpt is not None else candidate
                        clean_lines = "\n".join(line.strip() for line in candidate.splitlines())
                        reviewed_lines = "\n".join(line.strip() for line in reviewed_text.splitlines())
                        if f"\n{reviewed_lines}\n" not in f"\n{clean_lines}\n":
                            ocr_result["status"] = "needs_review"
                            reason = "Reviewed excerpt is not a contiguous range of complete OCR lines"
                        else:
                            ocr_result.update(status="approved", approval_scope="excerpt" if excerpt is not None else "whole_page")
                            clean = reviewed_text
                            extraction_method = "ocr_reviewed"
                            review_status = "verified"
                            status, reason = "passed", None
                    else:
                        ocr_result["status"] = "needs_review"
                        reason = ("OCR approval checksum does not match this candidate" if str(number) in ocr_reviews
                                  else "OCR candidate requires comparison with the source page")
                except (RuntimeError, OSError, ValueError) as exc:
                    ocr_result = {"status": "failed", "error": str(exc)}
                    reason = "OCR failed: " + str(exc)
            if reference_reviewed is not None and number not in reference_reviewed:
                status, review_status = "needs_review", "unverified"
                reason = reason or "Word reference PDF page mapping requires review"
            evidence_id = f"ev_{document['sha256'][:24]}_p{number}"
            section = next((line.strip()[:180] for line in clean.splitlines() if line.strip()), "")
            page = {
                "evidence_id": evidence_id, "file_id": document["file_id"],
                "pdf_page": number, "printed_page": None, "section": section,
                "raw_text": raw, "clean_text": clean,
                "bbox": list(source_page.rect), "coordinate_system": "pdf_points_top_left",
                "raw_blocks": blocks, "classification": classification,
                "image_fraction": image_fraction, "status": status,
                "review_status": review_status,
                "normalization": {"revision": "nfc-whitespace-v1", "changed": raw != clean},
            }
            if ocr_result is not None:
                page["ocr"] = ocr_result
            if extraction_method == "ocr_reviewed":
                page["extraction_method"] = extraction_method
                page["bbox"] = ocr_result["bbox"]
                page["coordinate_system"] = ocr_result["coordinate_system"]
            pages.append(page)
            dataset["raw_pages"].append(page)
            dataset["page_inventory"].append({k: page[k] for k in (
                "evidence_id", "file_id", "pdf_page", "classification", "status", "review_status")})
            if reason:
                self._issue(dataset, document["relative_path"], issue_step, reason,
                            "blocking", file_id=document["file_id"], pdf_page=number)
        return pages

    def _ocr_candidate(self, engine, page, document: dict, pdf_page: int) -> dict:
        contract = {"reference_sha256": document.get("reference_sha256", document["sha256"]),
                    "pdf_page": pdf_page, "engine": engine.contract, "dpi": 300}
        key = _digest(contract)
        cache = self.data_dir / "staging" / "ocr" / (key + ".json")
        if cache.exists():
            record = json.loads(cache.read_text(encoding="utf-8"))
            if record["contract"] != contract:
                raise ValueError("OCR cache contract mismatch")
            result = record["result"]
        else:
            result = engine.recognize(page, dpi=300)
            _write_json(cache, {"contract": contract, "result": result})
        return {**result, "candidate_sha256": _digest({"contract": contract, "result": result})}

    def _metadata(self, fields: dict, pages: list[dict], dataset: dict,
                  relative: str, file_id: str, defaults: bool = True) -> dict:
        if not isinstance(fields, dict):
            raise ValueError("Metadata fields must be an object")
        result = {field: {"value": None, "verification_status": "not_found", "evidence_ids": []}
                  for field in DEFAULT_METADATA_FIELDS} if defaults else {}
        by_number = {p["pdf_page"]: p for p in pages}
        by_evidence = {p["evidence_id"]: p for p in pages}
        for field, raw_value in fields.items():
            if not isinstance(raw_value, dict):
                raw_value = {"value": raw_value, "verification_status": "unverified"}
            state = raw_value.get("verification_status", "unverified")
            if state not in VERIFICATION_STATES:
                raise ValueError("Invalid metadata verification status: " + str(state))
            value = raw_value.get("value")
            evidence_ids = raw_value.get("evidence_ids", [])
            evidence_pages = raw_value.get("pages", [])
            if not isinstance(evidence_pages, list) or not isinstance(evidence_ids, list):
                raise ValueError("Metadata evidence_ids and pages must be lists")
            evidence_ids = list(evidence_ids)
            invalid = False
            for number in evidence_pages:
                if number not in by_number:
                    invalid = True
                else:
                    evidence_ids.append(by_number[number]["evidence_id"])
            evidence_ids = sorted(set(evidence_ids))
            if any(identifier not in by_evidence for identifier in evidence_ids):
                invalid = True
            if state == "verified":
                support = "\n".join(by_evidence[e]["clean_text"]
                                    for e in evidence_ids if e in by_evidence)
                # program_version_id is an assigned registry identifier rather
                # than a phrase to find verbatim; its scope must still cite
                # valid, explicitly reviewed source pages.
                assigned_id = field == "program_version_id"
                exact = value is not None and (
                    assigned_id or _normalized(value) in _normalized(support))
                valid_pages = bool(evidence_ids) and all(
                    by_evidence[e]["status"] == "passed" for e in evidence_ids if e in by_evidence)
                if invalid or not valid_pages or not exact:
                    state = "unverified"
                    self._issue(dataset, relative, "P1-08",
                                "Metadata not verified against eligible source evidence: " + field,
                                file_id=file_id)
            result[field] = {"value": value, "verification_status": state,
                             "evidence_ids": [e for e in evidence_ids if e in by_evidence]}
        return result
