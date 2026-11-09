"""HTTP and worker integration with real snapshots/indices and an injected local LLM."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from test_bundles import CONTRACT, FakeEncoder, make_pdf
from utc_rag.api import create_app
from utc_rag.bundles import BundleManager
from utc_rag.config import Settings
from utc_rag.ingestion import Ingestor
from utc_rag.retrieval import RetrievalIncomplete
from utc_rag.storage import read_json, write_json
from utc_rag.worker import run_one


class FakeLLM:
    def __init__(self):
        self.calls = 0
        self.failure = None

    def health(self):
        return {"ready": self.failure is None, "model": "test-local-model"}

    def generate(self, query, scope, selected):
        self.calls += 1
        if self.failure:
            raise self.failure
        return {"claims": [{"anchor": "E1", "quote": selected[0]["text"]}],
                "context": {"E1": selected[0]}, "omitted": [], "missing_information": [],
                "generation": {"model": "test-local-model"}}


class ApiTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / ".cache" / "test-tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="api_", dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.data = self.root / "sources", self.root / "data"
        self.configuration = Settings(data_dir=self.data, source_root=self.source,
                                      model_cache=self.root / "model-cache", admin_token="test-admin-secret",
                                      embedding_model=CONTRACT["model"])
        self.manager = BundleManager(self.configuration, embedder_factory=lambda contract: FakeEncoder())
        make_pdf(self.source / "training.pdf", "Major 7480201. Training curriculum and learning outcomes.")
        self.metadata = self.root / "metadata.json"
        write_json(self.metadata, {"files": {"training.pdf": {"fields": {"major_code": {
            "value": "7480201", "verification_status": "verified", "pages": [1]},
            "program_version_id": {"value": "utc-bsc-it-v1", "verification_status": "verified", "pages": [1]}}}}})
        self.job = Ingestor(self.data).ingest(self.source, self.metadata)
        self.identifier = self.manager.build(self.job["dataset_id"], "recursive", CONTRACT)["bundle_id"]
        self.manager.publish(self.identifier)
        self.llm = FakeLLM()
        self.client = TestClient(create_app(self.configuration, self.manager, self.llm))
        self.addCleanup(self.client.close)
        self.auth = {"Authorization": "Bearer test-admin-secret"}

    def request(self, route="/search", **extra):
        if route == "/ask" and "filters" not in extra:
            extra["filters"] = {"program_version_id": "utc-bsc-it-v1"}
        return self.client.post(route, json={"query": "training curriculum", **extra})

    def assert_traced(self, response, expected):
        body = response.json()
        record = read_json(self.data / "traces" / (body["request_id"] + ".json"))
        self.assertEqual(record["response"]["status"], expected)
        return body, record

    def test_search_returns_registered_source_without_calling_llm(self):
        response = self.request()
        self.assertEqual(response.status_code, 200)
        body, record = self.assert_traced(response, "answered")
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(body["bundle_id"], self.identifier)
        self.assertTrue(body["retrieved_chunks"])
        self.assertIn("bm25", record["retrieval"])
        citation = body["citations"][0]
        source = self.client.get(citation["url"])
        self.assertEqual(source.status_code, 200)
        self.assertEqual(source.headers["content-type"], "application/pdf")
        self.assertTrue(source.content.startswith(b"%PDF"))

    def test_ask_uses_llm_and_only_returns_context_quote_citations(self):
        response = self.request("/ask")
        self.assertEqual(response.status_code, 200)
        body, _ = self.assert_traced(response, "partial")
        self.assertEqual(self.llm.calls, 1)
        self.assertEqual(body["citations"][0]["quote"], body["retrieved_chunks"][0]["text"])
        self.assertTrue(body["used_evidence"])
        self.assertIn("[E1]", body["answer"])

    def test_ollama_failure_is_technical_error_without_answer_fallback(self):
        self.llm.failure = RuntimeError("local inference unavailable")
        response = self.request("/ask")
        self.assertEqual(response.status_code, 503)
        body, record = self.assert_traced(response, "error")
        self.assertEqual(body["error_code"], "local_service_error")
        self.assertEqual(body["citations"], [])
        self.assertIn("local inference unavailable", record["technical_error"]["message"])

    def test_unknown_filter_is_422_and_does_not_call_model(self):
        response = self.request("/ask", filters={"major_code": "unverified-code"})
        self.assertEqual(response.status_code, 422)
        self.assert_traced(response, "error")
        self.assertEqual(self.llm.calls, 0)

    def test_ask_requires_explicit_verified_program_selection(self):
        response = self.request("/ask", filters={})
        self.assertEqual(response.status_code, 200)
        body, _ = self.assert_traced(response, "clarification_required")
        self.assertEqual(body["clarification"], {"field": "program_version_id", "options": ["utc-bsc-it-v1"]})
        self.assertIn("total_ms", body["timings"])
        self.assertEqual(self.llm.calls, 0)

    def test_retrieval_timeout_is_incomplete_with_trace_and_no_model(self):
        partial = {"bm25": [{"chunk_id": "completed_lexical_candidate", "score": 1.2}], "eligible_count": 1}
        for route in ("/search", "/ask"):
            with self.subTest(route=route), patch("utc_rag.api.retrieve", side_effect=RetrievalIncomplete("faiss", partial)):
                response = self.request(route)
                self.assertEqual(response.status_code, 200)
                body, record = self.assert_traced(response, "retrieval_incomplete")
                self.assertEqual(body["reason"], "timeout")
                self.assertEqual(record["retrieval"], partial)
                self.assertEqual(body["retrieved_chunks"], [])
                self.assertEqual(body["citations"], [])
                self.assertTrue(body["warnings"])
                self.assertIn("total_ms", body["timings"])
        self.assertEqual(self.llm.calls, 0)

    def test_unverified_totals_full_course_lists_and_effectiveness_do_not_call_model(self):
        queries = ["Tổng tín chỉ của chương trình là bao nhiêu?",
                   "Cho biết toàn bộ học phần của chương trình?",
                   "Quyết định này đang áp dụng và còn hiệu lực không?"]
        with patch("utc_rag.api.retrieve") as search:
            for query in queries:
                with self.subTest(query=query):
                    response = self.request("/ask", query=query)
                    self.assertEqual(response.status_code, 200)
                    body, _ = self.assert_traced(response, "insufficient_evidence")
                    self.assertTrue(body["missing_information"])
                    self.assertEqual(body["citations"], [])
                    self.assertIn("total_ms", body["timings"])
        search.assert_not_called()
        self.assertEqual(self.llm.calls, 0)

    def test_no_verified_program_is_insufficient_evidence_before_model(self):
        dataset = Ingestor(self.data).ingest(self.source)
        bundle = self.manager.build(dataset["dataset_id"], "recursive", CONTRACT)["bundle_id"]
        self.manager.publish(bundle, expected_current=self.identifier)
        response = self.request("/ask", filters={})
        self.assertEqual(response.status_code, 200)
        self.assert_traced(response, "insufficient_evidence")
        self.assertEqual(self.llm.calls, 0)

    def test_schema_validation_has_request_id_and_error_trace(self):
        response = self.request("/ask", query=" ", strategy="unsupported")
        self.assertEqual(response.status_code, 422)
        body = response.json()
        trace = read_json(self.data / "traces" / (body["request_id"] + ".json"))
        self.assertEqual(trace["status"], "error")
        self.assertEqual(trace["reason"], "invalid_input")
        self.assertEqual(self.llm.calls, 0)

    def test_missing_strategy_bundle_is_503_and_traced(self):
        response = self.request("/ask", strategy="fixed")
        self.assertEqual(response.status_code, 503)
        body, _ = self.assert_traced(response, "error")
        self.assertEqual(body["error_code"], "bundle_unavailable")
        self.assertEqual(self.llm.calls, 0)

    def test_corrupt_bundle_is_technical_error_and_not_ready(self):
        index = self.manager.path(self.identifier) / "index.faiss"
        index.write_bytes(b"corrupt deployed index")
        health = self.client.get("/health").json()
        self.assertTrue(health["alive"])
        self.assertFalse(health["ready"])
        response = self.request()
        self.assertEqual(response.status_code, 503)
        body, _ = self.assert_traced(response, "error")
        self.assertNotEqual(body["error_code"], "invalid_scope_or_bundle")
        self.assertEqual(body["citations"], [])

    def test_explicit_wrong_strategy_bundle_is_422(self):
        response = self.request(strategy="fixed", index_version=self.identifier)
        self.assertEqual(response.status_code, 422)
        self.assert_traced(response, "error")

    def test_admin_requires_valid_authorization(self):
        self.assertEqual(self.client.get("/admin/jobs").status_code, 401)
        self.assertEqual(self.client.get("/admin/releases", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        self.assertEqual(self.client.get("/admin/jobs", headers=self.auth).status_code, 200)

    def test_admin_ingest_idempotency_and_separate_worker(self):
        payload = {"source_path": str(self.source), "idempotency_key": "ingest-once"}
        first = self.client.post("/admin/jobs", json=payload, headers=self.auth)
        second = self.client.post("/admin/jobs", json=payload, headers=self.auth)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()["status"], "pending")
        self.assertEqual(second.json()["job_id"], first.json()["job_id"])
        self.assertTrue(second.json()["reused"])
        self.assertEqual(len(self.manager.state.jobs()), 1)
        self.assertTrue(run_one(self.manager))
        state = self.client.get("/admin/jobs/" + first.json()["job_id"], headers=self.auth).json()
        self.assertEqual(state["status"], "completed")
        self.assertIn("dataset_id", state)
        self.assertFalse(run_one(self.manager))

    def test_admin_build_queue_then_release_conflict(self):
        write_json(self.data / "embedding_contract.json", CONTRACT)
        queued = self.client.post("/admin/build", json={"dataset_id": self.job["dataset_id"], "strategy": "fixed"}, headers=self.auth)
        self.assertEqual(queued.status_code, 202)
        self.assertEqual(queued.json()["status"], "pending")
        self.assertTrue(run_one(self.manager))
        job = self.client.get("/admin/jobs/" + queued.json()["job_id"], headers=self.auth).json()
        self.assertEqual(job["status"], "completed")
        published = self.client.post("/admin/releases", json={"bundle_id": job["bundle_id"], "expected_current": None}, headers=self.auth)
        self.assertEqual(published.status_code, 200)
        conflict = self.client.post("/admin/releases", json={"bundle_id": job["bundle_id"], "expected_current": "wrong-current"}, headers=self.auth)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.manager.current()["fixed"], job["bundle_id"])

    def test_source_page_bundle_and_id_must_belong_to_published_scope(self):
        doc = self.manager.load(self.identifier).dataset["documents"][0]
        route = "/sources/" + doc["file_id"]
        self.assertEqual(self.client.get(route, params={"bundle_id": self.identifier, "page": 2}).status_code, 404)
        self.assertEqual(self.client.get(route, params={"bundle_id": "unknown", "page": 1}).status_code, 404)
        self.assertEqual(self.client.get("/sources/private.pdf", params={"bundle_id": self.identifier}).status_code, 404)
        self.assertEqual(self.client.get(route, params={"bundle_id": "../private", "page": 1}).status_code, 404)
        unpublished = self.manager.build(self.job["dataset_id"], "fixed", CONTRACT)["bundle_id"]
        self.assertEqual(self.client.get(route, params={"bundle_id": unpublished}).status_code, 404)

    def test_admin_source_paths_outside_allowlist_are_rejected(self):
        response = self.client.post("/admin/jobs", json={"source_path": str(self.root), "idempotency_key": "outside"}, headers=self.auth)
        self.assertEqual(response.status_code, 422)
        response = self.client.post("/admin/jobs", json={"source_path": str(self.source), "metadata_path": str(self.metadata),
                                                        "idempotency_key": "bad-metadata"}, headers=self.auth)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.manager.state.jobs(), [])

    def test_versions_excludes_drafts(self):
        draft = self.manager.build(self.job["dataset_id"], "fixed", CONTRACT)["bundle_id"]
        public_ids = [v["bundle_id"] for v in self.client.get("/versions").json()["versions"]]
        admin_ids = [v["bundle_id"] for v in self.client.get("/admin/releases", headers=self.auth).json()["versions"]]
        self.assertNotIn(draft, public_ids)
        self.assertIn(draft, admin_ids)


if __name__ == "__main__":
    unittest.main()
