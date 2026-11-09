from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import threading
import uuid
import numpy as np

from .chunking import build_chunks
from .retrieval import Embedder, make_bm25, metadata_value, tokenize
from .storage import State, digest, now, read_json, write_json


def source_checksums(dataset):
    checksums = {}
    for document in dataset["documents"]:
        if not document.get("reference_path"):
            continue
        checksums[document["reference_path"]] = document.get("reference_sha256", document["sha256"])
        checksums[document["snapshot_path"]] = document["sha256"]
        origin = document.get("archive_origin")
        if origin:
            checksums[origin["snapshot_path"]] = origin["sha256"]
    return checksums


@dataclass(frozen=True)
class Bundle:
    manifest: dict
    dataset: dict
    chunks: list
    vectors: np.ndarray
    bm25: object
    embedder: object

    @property
    def bundle_id(self):
        return self.manifest["bundle_id"]


class BundleManager:
    def __init__(self, settings, embedder_factory=None):
        self.settings = settings
        settings.prepare()
        self.root = settings.data_dir
        self.state = State(self.root)
        self.embedder_factory = embedder_factory or (lambda contract: Embedder(contract, settings.model_cache))
        self.lock = threading.RLock()
        self.cache = {}

    def current(self):
        return self.state.current()

    def path(self, identifier, kind="bundles"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,120}", identifier):
            raise ValueError("Định danh phiên bản không hợp lệ.")
        return self.root / kind / identifier

    def safe_source(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise ValueError("Nguồn không thuộc kho hoặc không tồn tại.")
        return path

    def build(self, dataset_id, strategy, contract=None):
        if strategy not in ("fixed", "recursive"):
            raise ValueError("Chiến lược không hợp lệ.")
        dataset = read_json(self.path(dataset_id, "datasets") / "dataset.json")
        chunks = build_chunks(dataset, strategy, self.settings.chunk_size, self.settings.overlap)
        if not chunks:
            raise ValueError("Dataset chưa có nội dung đủ điều kiện để lập chỉ mục.")
        contract_path = self.root / "embedding_contract.json"
        if contract is None:
            if contract_path.exists():
                contract = read_json(contract_path)
                if contract["model"] != self.settings.embedding_model:
                    raise ValueError("Model embedding cấu hình khác hợp đồng đã khóa; cần tải hợp đồng mới.")
            else:
                contract = Embedder.download(self.settings.embedding_model, self.settings.embedding_revision, self.settings.model_cache)
                write_json(contract_path, contract)
        encoder = self.embedder_factory(contract)
        config = {"strategy": strategy, "chunk_size": self.settings.chunk_size,
                  "overlap": self.settings.overlap, "unit": "unicode_characters", "pipeline_revision": "utc-rag-0.1.0",
                  "bm25": {"k1": 1.5, "b": 0.75, "idf": "log(1+(N-df+0.5)/(df+0.5))", "tokenizer": "nfc_unicode_codes_v1"},
                  "retrieval": {"bm25_k": 8, "faiss_k": 8, "rrf_k": 60, "context_k": 5}}
        identity = json.dumps([dataset_id, config, contract, [c["chunk_id"] for c in chunks]], sort_keys=True)
        bundle_id = "b_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        target = self.path(bundle_id)
        if target.exists():
            return self.load(bundle_id).manifest
        vectors = encoder.encode([chunk["text"] for chunk in chunks])
        temporary = self.root / "bundles" / ("building_" + uuid.uuid4().hex)
        temporary.mkdir()
        ids = [chunk["chunk_id"] for chunk in chunks]
        write_json(temporary / "dataset.json", dataset)
        write_json(temporary / "chunks.json", chunks)
        write_json(temporary / "bm25.json", {"ids": ids, "tokens": [tokenize(c["text"]) for c in chunks]})
        write_json(temporary / "vector_ids.json", ids)
        np.save(temporary / "vectors.npy", vectors, allow_pickle=False)
        import faiss
        index = faiss.IndexFlatIP(contract["dimension"])
        index.add(np.ascontiguousarray(vectors, dtype="float32"))
        faiss.write_index(index, str(temporary / "index.faiss"))
        manifest = {"schema_version": 1, "bundle_id": bundle_id, "index_version": bundle_id,
                    "dataset_version": dataset.get("dataset_version", dataset_id), "chunk_set_id": "cs_" + bundle_id[2:],
                    "strategy": strategy, "created_at": now(), "state": "qa_passed", "config": config,
                    "embedding": contract, "coverage": dataset["coverage"], "chunk_count": len(chunks),
                    "document_count": len({p["file_id"] for p in dataset["pages"]}),
                    "checksums": {p.name: digest(p) for p in temporary.iterdir() if p.is_file()},
                    "source_checksums": source_checksums(dataset)}
        write_json(temporary / "manifest.json", manifest)
        self._verify(temporary)
        with self.lock:
            if not target.exists():
                temporary.rename(target)
        return self.load(bundle_id).manifest

    def _verify(self, directory):
        manifest = read_json(directory / "manifest.json")
        if manifest["schema_version"] != 1 or manifest["state"] != "qa_passed":
            raise ValueError("Bundle chưa đạt schema/QA.")
        required = {"dataset.json", "chunks.json", "bm25.json", "vector_ids.json", "vectors.npy", "index.faiss"}
        if set(manifest["checksums"]) != required:
            raise ValueError("Manifest thiếu thành phần bắt buộc.")
        for name, expected in manifest["checksums"].items():
            if Path(name).name != name or digest(directory / name) != expected:
                raise ValueError("Checksum bundle không khớp: " + name)
        for name, expected in manifest["source_checksums"].items():
            if digest(self.safe_source(name)) != expected:
                raise ValueError("Checksum nguồn không khớp.")
        chunks = read_json(directory / "chunks.json")
        keyword = read_json(directory / "bm25.json")
        vector_ids = read_json(directory / "vector_ids.json")
        ids = [c["chunk_id"] for c in chunks]
        if not ids or len(set(ids)) != len(ids) or ids != keyword["ids"] or ids != vector_ids:
            raise ValueError("Tập ID chunk/BM25/FAISS không đồng bộ.")
        if len(keyword["tokens"]) != len(ids) or any(tokens != tokenize(c["text"]) for tokens, c in zip(keyword["tokens"], chunks)):
            raise ValueError("Chỉ mục BM25 không khớp nội dung chunk.")
        vectors = np.load(directory / "vectors.npy", allow_pickle=False)
        if vectors.shape != (len(ids), manifest["embedding"]["dimension"]) or not np.isfinite(vectors).all():
            raise ValueError("Chiều hoặc giá trị vector sai.")
        if not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-3):
            raise ValueError("Vector chưa được chuẩn hóa.")
        import faiss
        index = faiss.read_index(str(directory / "index.faiss"))
        if index.ntotal != len(ids) or index.d != vectors.shape[1] or index.metric_type != faiss.METRIC_INNER_PRODUCT:
            raise ValueError("FAISS không khớp hợp đồng.")
        if not np.allclose(index.reconstruct_n(0, index.ntotal), vectors, atol=1e-6):
            raise ValueError("FAISS không khớp kho vector.")
        dataset = read_json(directory / "dataset.json")
        documents = {d["file_id"]: d for d in dataset["documents"]}
        pages = {p["evidence_id"]: p for p in dataset["pages"]}
        if manifest["source_checksums"] != source_checksums(dataset):
            raise ValueError("Manifest thiếu hoặc sai checksum nguồn.")
        for chunk in chunks:
            if not chunk["text"].strip() or not chunk["source_spans"]:
                raise ValueError("Chunk không có nội dung/nguồn.")
            for span in chunk["source_spans"]:
                page = pages.get(span["evidence_id"])
                doc = documents.get(span["file_id"])
                if not page or not doc or page["file_id"] != doc["file_id"] or page["pdf_page"] != span["pdf_page"]:
                    raise ValueError("Evidence tham chiếu không hợp lệ.")
                if not 1 <= span["pdf_page"] <= doc["page_count"] or not doc.get("reference_path"):
                    raise ValueError("Citation chưa có PDF/trang hợp lệ.")
                if page.get("extraction_method") == "ocr_reviewed" and (page.get("review_status") != "verified" or page.get("ocr", {}).get("status") != "approved"):
                    raise ValueError("Trang OCR chưa được đối chiếu.")
                start, end = span.get("char_start"), span.get("char_end")
                if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(page["clean_text"]):
                    raise ValueError("Span vượt nội dung trang.")
                if len(chunk["source_spans"]) == 1 and chunk["text"] != page["clean_text"][start:end]:
                    raise ValueError("Nội dung chunk không khớp span nguồn.")
        return manifest, dataset, chunks, vectors, keyword

    def load(self, bundle_id):
        directory = self.path(bundle_id)
        manifest, dataset, chunks, vectors, keyword = self._verify(directory)
        if manifest["bundle_id"] != bundle_id:
            raise ValueError("Bundle ID không khớp manifest.")
        with self.lock:
            if bundle_id not in self.cache:
                self.cache[bundle_id] = Bundle(manifest, dataset, chunks, vectors, make_bm25(keyword["tokens"]),
                                               self.embedder_factory(manifest["embedding"]))
            return self.cache[bundle_id]

    def resolve(self, strategy, version=None):
        identifier = version or self.current().get(strategy)
        if not identifier:
            raise FileNotFoundError("Chưa có bundle được phát hành cho chiến lược này.")
        if not self.state.published(identifier):
            raise ValueError("Bundle chưa được công bố.")
        bundle = self.load(identifier)
        if bundle.manifest["strategy"] != strategy:
            raise ValueError("Chiến lược và phiên bản không tương thích.")
        return bundle

    def publish(self, bundle_id, expected_current=None, rollback=False):
        with self.lock:
            bundle = self.load(bundle_id)
            # Source and retrieval smoke check happens before the transactional pointer switch.
            from .retrieval import retrieve
            if not retrieve(bundle, bundle.chunks[0]["text"][:100], {}, 1)["results"]:
                raise ValueError("Smoke query không trả nguồn.")
            self.state.switch(bundle.manifest["strategy"], bundle_id, expected_current,
                              "rollback" if rollback else "publish")
        return {"bundle_id": bundle_id, "strategy": bundle.manifest["strategy"], "current": self.current()}

    def versions(self):
        current = self.current()
        versions = []
        for path in sorted((self.root / "bundles").glob("b_*/manifest.json")):
            item = read_json(path)
            versions.append({**{k: item[k] for k in ("bundle_id", "strategy", "dataset_version", "created_at", "chunk_count", "document_count", "coverage")},
                             "published": self.state.published(item["bundle_id"]),
                             "active": current.get(item["strategy"]) == item["bundle_id"]})
        return {"versions": versions, "current": current}

    def catalog(self, bundle):
        keys = ("major_code", "degree_level", "program_type", "program_version_id", "document_type", "number_symbol")
        filters = {key: sorted({str(value) for c in bundle.chunks if (value := metadata_value(c.get("metadata", {}), key)) is not None}) for key in keys}
        eligible_pages = {}
        for page in bundle.dataset["pages"]:
            eligible_pages.setdefault(page["file_id"], []).append(page["pdf_page"])
        return {"bundle_id": bundle.bundle_id, "strategy": bundle.manifest["strategy"], "filters": filters,
                "documents": [{**{k: doc.get(k) for k in ("file_id", "title", "original_name", "page_count", "metadata")},
                               "first_eligible_page": min(eligible_pages[doc["file_id"]]),
                               "eligible_page_count": len(eligible_pages[doc["file_id"]])}
                              for doc in bundle.dataset["documents"] if doc["file_id"] in eligible_pages], "coverage": bundle.manifest["coverage"]}
