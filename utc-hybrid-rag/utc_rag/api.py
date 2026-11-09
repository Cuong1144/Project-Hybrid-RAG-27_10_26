import json
from pathlib import Path
import secrets
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .bundles import BundleManager
from .config import ROOT, Settings, settings
from .generation import Ollama
from .retrieval import RetrievalIncomplete, retrieve
from .storage import now, write_json


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=2, max_length=2000)
    filters: dict[str, str] = Field(default_factory=dict)
    strategy: str = Field(default="recursive", pattern="^(fixed|recursive)$")
    index_version: str | None = Field(default=None, max_length=120)
    top_k: int = Field(default=8, ge=1, le=16)

    @field_validator("query")
    @classmethod
    def trim_query(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Câu hỏi cần ít nhất 2 ký tự.")
        return value

    @field_validator("filters")
    @classmethod
    def validate_filters(cls, value):
        allowed = {"major_code", "degree_level", "program_type", "program_version_id", "document_type", "number_symbol"}
        if set(value) - allowed or any(not v.strip() or len(v) > 200 for v in value.values()):
            raise ValueError("Bộ lọc không hợp lệ.")
        return value


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_path: str = Field(min_length=1, max_length=1024)
    metadata_path: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=120)
    ocr: bool = False
    expand_archives: bool = False


class BuildRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,120}$")
    strategy: str = Field(pattern="^(fixed|recursive)$")


class ReleaseRequest(BaseModel):
    bundle_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,120}$")
    expected_current: str | None = Field(...)


def create_app(configuration: Settings = settings, manager=None, llm=None):
    manager = manager or BundleManager(configuration)
    llm = llm or Ollama(configuration)
    app = FastAPI(title="Cổng tra cứu thông tin đào tạo UTC", version="0.1.0")
    app.state.manager, app.state.llm = manager, llm

    def admin(authorization: str | None = Header(default=None)):
        if not configuration.admin_token:
            raise HTTPException(503, "Quản trị chưa cấu hình UTC_ADMIN_TOKEN.")
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not secrets.compare_digest(supplied, configuration.admin_token):
            raise HTTPException(401, "Cần mã truy cập quản trị hợp lệ.")
        return "operator"

    def trace(body):
        write_json(configuration.data_dir / "traces" / (body["request_id"] + ".json"), body)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc):
        request_id = uuid.uuid4().hex
        trace({"request_id": request_id, "created_at": now(), "status": "error", "reason": "invalid_input", "path": request.url.path})
        return JSONResponse(status_code=422, content={"request_id": request_id, "status": "error", "error_code": "invalid_input",
            "detail": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]})

    @app.get("/health")
    def health():
        current = manager.current()
        ready = bool(current)
        try:
            for identifier in current.values():
                manager._verify(manager.path(identifier))
        except Exception:
            ready = False
        return {"alive": True, "ready": ready, "bundle_count": len(current), "current": current,
                "llm": llm.health(), "worker": "separate_process"}

    @app.get("/versions")
    def versions():
        result = manager.versions()
        result["versions"] = [v for v in result["versions"] if v["published"]]
        return result

    @app.get("/catalog")
    def catalog(strategy: str = "recursive", index_version: str | None = None):
        try:
            return manager.catalog(manager.resolve(strategy, index_version))
        except FileNotFoundError:
            raise HTTPException(503, "Chưa có bộ dữ liệu được công bố.")
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    def citation(bundle, chunk, anchor, quote=None):
        span = chunk["source_spans"][0]
        return {"anchor": anchor, "chunk_id": chunk["chunk_id"], "evidence_ids": [s["evidence_id"] for s in chunk["source_spans"]],
                "file_id": span["file_id"], "title": chunk["title"], "pdf_page": span["pdf_page"],
                "section": span.get("section"), "quote": quote or chunk["text"],
                "url": f"/sources/{span['file_id']}?bundle_id={bundle.bundle_id}&page={span['pdf_page']}#page={span['pdf_page']}"}

    def process(payload, ask):
        started = time.perf_counter()
        result = {"request_id": uuid.uuid4().hex, "status": "error", "answer": "", "scope_resolved": payload.filters,
                  "bundle_id": None, "dataset_version": None, "index_version": None, "strategy": payload.strategy,
                  "citations": [], "retrieved_chunks": [], "used_evidence": [], "missing_information": [], "warnings": [], "timings": {}}
        record = {"request_id": result["request_id"], "created_at": now(), "query": payload.query,
                  "filters": payload.filters, "operation": "ask" if ask else "search"}
        http_status = 200
        loading_bundle = True
        try:
            bundle = manager.resolve(payload.strategy, payload.index_version)
            loading_bundle = False
            result.update(bundle_id=bundle.bundle_id, dataset_version=bundle.manifest["dataset_version"], index_version=bundle.bundle_id)
            catalog = manager.catalog(bundle)
            for key, value in payload.filters.items():
                if value not in catalog["filters"].get(key, []):
                    raise ValueError("Giá trị lọc chưa được xác minh trong phiên bản này: " + key)
            programs = catalog["filters"]["program_version_id"]
            if ask and programs and not payload.filters.get("program_version_id"):
                result.update(status="clarification_required", answer="Bạn cần tra cứu phiên bản chương trình nào?",
                              clarification={"field": "program_version_id", "options": programs})
                result["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
                return JSONResponse(result)
            if ask and not programs:
                result.update(status="insufficient_evidence", answer="Chưa có phạm vi chương trình được xác minh để hỏi đáp. Bạn có thể tìm tài liệu và kiểm tra nguồn.")
                result["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
                return JSONResponse(result)
            if ask and any(term in payload.query.casefold() for term in ("tổng tín chỉ", "toàn bộ học phần", "tất cả học phần", "đang áp dụng", "hiệu lực", "mới nhất")):
                result.update(status="insufficient_evidence", answer="Kho thử nghiệm chưa có bảng chương trình đầy đủ hoặc metadata hiệu lực đã xác minh cho yêu cầu này.",
                              missing_information=["Cần nguồn và quy tắc áp dụng đã được kiểm tra."])
                result["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
                return JSONResponse(result)
            retrieval_start = time.perf_counter()
            found = retrieve(bundle, payload.query, payload.filters, 16)
            result["timings"]["retrieval_ms"] = round((time.perf_counter() - retrieval_start) * 1000, 2)
            record["retrieval"] = found
            result["retrieved_chunks"] = found["results"][:payload.top_k]
            if not found["results"]:
                result.update(status="insufficient_evidence", answer="Chưa tìm thấy bằng chứng trong phạm vi đã chọn.")
            elif not ask:
                result.update(status="answered", answer="Đã tìm thấy các đoạn liên quan để bạn kiểm tra nguồn.",
                              citations=[citation(bundle, chunk, "E" + str(i + 1)) for i, chunk in enumerate(result["retrieved_chunks"])])
            else:
                # RRF ranks candidates; it is never interpreted as answer confidence.
                selected = []
                seen = set()
                for chunk in found["results"]:
                    span = chunk["source_spans"][0]
                    key = (span["file_id"], span["pdf_page"], span["char_start"])
                    if key in seen:
                        continue
                    if chunk.get("bm25_score", 0) <= 0 and chunk.get("faiss_score", 0) < 0.78:
                        continue
                    selected.append(chunk)
                    seen.add(key)
                    if len(selected) == 5:
                        break
                if not selected:
                    result.update(status="insufficient_evidence", answer="Các đoạn tìm được chưa đủ liên quan để trả lời có căn cứ.")
                else:
                    pages = {page["evidence_id"]: page for page in bundle.dataset["pages"]}
                    selected = [{**chunk, "source_page_text": pages[chunk["source_spans"][0]["evidence_id"]]["clean_text"]} for chunk in selected]
                    generation_start = time.perf_counter()
                    generated = llm.generate(payload.query, payload.filters, selected)
                    result["timings"]["generation_ms"] = round((time.perf_counter() - generation_start) * 1000, 2)
                    record["generation"] = generated
                    if generated["missing_information"]:
                        result["missing_information"] = ["Bằng chứng đã chọn chưa đủ để xác nhận mọi phần của câu hỏi."]
                    for claim in generated["claims"]:
                        chunk = generated["context"][claim["anchor"]]
                        result["citations"].append(citation(bundle, chunk, claim["anchor"], claim["quote"]))
                    result["used_evidence"] = sorted({e for c in result["citations"] for e in c["evidence_ids"]})
                    if result["citations"]:
                        result["answer"] = "\n\n".join(c["quote"] + " [" + c["anchor"] + "]" for c in result["citations"])
                        result["status"] = "partial"
                        result["warnings"].append("Câu trả lời gồm trích đoạn nguyên văn đã kiểm tra. Tính đầy đủ và khả năng áp dụng cần đối chiếu phạm vi nguồn.")
                        if generated["omitted"]:
                            result["warnings"].append("Một số bằng chứng chưa được gửi do giới hạn context (context_budget).")
                    else:
                        result.update(status="insufficient_evidence", answer="Bằng chứng được chọn chưa đủ để trả lời câu hỏi.")
            if bundle.manifest["coverage"].get("blocked_files") or bundle.manifest["coverage"].get("blocked_pages"):
                result["warnings"].append("Kho hiện chỉ phục vụ phần tài liệu đủ điều kiện; xem giới hạn trong danh mục.")
        except RetrievalIncomplete as exc:
            record["retrieval"] = exc.partial
            result.update(status="retrieval_incomplete", reason="timeout", answer="Truy xuất đã dừng do quá thời gian trước khi hoàn tất cả hai nhánh.",
                          warnings=["Chưa tìm xong phạm vi; kết quả này không chứng minh thông tin không tồn tại."])
        except FileNotFoundError:
            http_status = 503
            result.update(status="error", error_code="bundle_unavailable", answer="Chưa có bundle hoặc tài nguyên local sẵn sàng.")
        except ValueError as exc:
            if loading_bundle and not any(word in str(exc) for word in ("Chiến lược", "chưa được công bố", "Định danh")):
                http_status = 503
                result.update(status="error", error_code="bundle_integrity_error", answer="Bundle không đạt kiểm tra toàn vẹn. Cần kiểm tra nhật ký quản trị.")
                record["technical_error"] = str(exc)
            else:
                http_status = 422
                result.update(status="error", error_code="invalid_scope_or_bundle", answer=str(exc))
        except TimeoutError:
            http_status = 429
            result.update(status="error", error_code="llm_busy", answer="LLM đang bận. Hãy thử lại sau.")
        except Exception as exc:
            http_status = 503
            record["technical_error"] = {"type": type(exc).__name__, "message": str(exc)}
            result.update(status="error", error_code="local_service_error", answer="Dịch vụ local hoặc dữ liệu chưa sẵn sàng. Dùng mã yêu cầu để kiểm tra nhật ký quản trị.")
        finally:
            result["timings"]["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
            record["response"] = result
            try:
                trace(record)
            except OSError:
                result["warnings"].append("Không lưu được nhật ký yêu cầu; cần kiểm tra kho log.")
        return JSONResponse(result, status_code=http_status)

    @app.post("/search")
    def search(payload: Query):
        return process(payload, False)

    @app.post("/ask")
    def ask(payload: Query):
        return process(payload, True)

    @app.get("/sources/{file_id}")
    def source(file_id: str, bundle_id: str, page: int = 1):
        try:
            if not manager.state.published(bundle_id):
                raise HTTPException(404, "Nguồn không có trong phiên bản đã công bố.")
            bundle = manager.load(bundle_id)
            document = next((d for d in bundle.dataset["documents"] if d["file_id"] == file_id), None)
            eligible_pages = {p["pdf_page"] for p in bundle.dataset["pages"] if p["file_id"] == file_id}
            if not document or not document.get("reference_path") or page not in eligible_pages:
                raise HTTPException(404, "Nguồn hoặc trang không thuộc phạm vi phục vụ.")
            return FileResponse(manager.safe_source(document["reference_path"]), media_type="application/pdf",
                                filename=document.get("reference_name", document["original_name"]), content_disposition_type="inline")
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "Nguồn hoặc phiên bản không hợp lệ.")

    @app.get("/admin/jobs", dependencies=[Depends(admin)])
    def jobs():
        return {"jobs": manager.state.jobs()}

    @app.get("/admin/jobs/{job_id}", dependencies=[Depends(admin)])
    def job(job_id: str):
        return next((j for j in manager.state.jobs() if j["job_id"] == job_id), None) or JSONResponse({"detail": "Không có job."}, status_code=404)

    @app.post("/admin/jobs", dependencies=[Depends(admin)], status_code=202)
    def submit(payload: IngestRequest):
        source = Path(payload.source_path).resolve()
        if not source.is_relative_to(configuration.source_root) or not source.exists():
            raise HTTPException(422, "Nguồn phải nằm trong UTC_SOURCE_ROOT đã cấu hình.")
        metadata = None
        if payload.metadata_path:
            metadata = Path(payload.metadata_path).resolve()
            if not metadata.is_relative_to(ROOT / "config") or not metadata.is_file():
                raise HTTPException(422, "Metadata phải là file trong thư mục config của dự án.")
        with manager.state.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT job_id FROM requests WHERE idempotency_key=?", (payload.idempotency_key,)).fetchone()
            if existing:
                return {"job_id": existing[0], "reused": True}
            identifier = "job_" + uuid.uuid4().hex
            record = {"job_id": identifier, "kind": "ingest", "status": "pending", "created_at": now(),
                      "source_path": str(source), "metadata_path": str(metadata) if metadata else None,
                      "ocr": payload.ocr, "expand_archives": payload.expand_archives}
            db.execute("INSERT INTO requests VALUES (?,?)", (payload.idempotency_key, identifier))
            db.execute("INSERT INTO jobs VALUES (?,?)", (identifier, json.dumps(record, ensure_ascii=False)))
        return record

    @app.post("/admin/build", dependencies=[Depends(admin)], status_code=202)
    def build(payload: BuildRequest):
        if not (manager.path(payload.dataset_id, "datasets") / "dataset.json").exists():
            raise HTTPException(404, "Dataset chưa tồn tại.")
        record = {"job_id": "build_" + uuid.uuid4().hex, "kind": "build", "status": "pending", "created_at": now(), **payload.model_dump()}
        manager.state.save_job(record)
        return record

    @app.get("/admin/releases", dependencies=[Depends(admin)])
    def releases():
        return {**manager.versions(), "releases": manager.state.releases()}

    def change_release(payload, rollback):
        try:
            return manager.publish(payload.bundle_id, payload.expected_current, rollback)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(409, str(exc))

    @app.post("/admin/releases", dependencies=[Depends(admin)])
    def release(payload: ReleaseRequest):
        return change_release(payload, False)

    @app.post("/admin/rollback", dependencies=[Depends(admin)])
    def rollback(payload: ReleaseRequest):
        return change_release(payload, True)

    app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "web" / "index.html")

    return app


app = create_app()
