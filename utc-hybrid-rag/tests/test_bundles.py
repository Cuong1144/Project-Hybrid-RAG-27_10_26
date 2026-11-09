"""Persisted bundle, release and evidence integrity using actual FAISS files."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pymupdf

from utc_rag.bundles import BundleManager
from utc_rag.config import Settings
from utc_rag.ingestion import Ingestor
from utc_rag.retrieval import tokenize
from utc_rag.storage import digest, read_json, write_json


CONTRACT = {"model": "unit-test-encoder", "revision": "immutable-test-revision", "dimension": 8,
            "metric": "inner_product", "normalize": True, "query_instruction": "query: ",
            "document_instruction": "passage: ", "tokenizer_revision": "immutable-test-revision"}


class FakeEncoder:
    """Deterministic test double, only injected by these tests; never a runtime fallback."""
    def encode(self, texts, query=False):
        values = []
        for text in texts:
            raw = hashlib.sha256(text.encode()).digest()[:8]
            row = np.asarray([b + 1 for b in raw], dtype="float32")
            values.append(row / np.linalg.norm(row))
        return np.asarray(values, dtype="float32")


def make_pdf(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 70), text)
        pdf.save(path)


class BundleTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / ".cache" / "test-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="bundles_", dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "sources"
        self.data = self.root / "data"
        self.config = Settings(data_dir=self.data, source_root=self.source,
                               model_cache=self.root / "model-cache", chunk_size=120, overlap=25)
        self.manager = BundleManager(self.config, embedder_factory=lambda contract: FakeEncoder())
        make_pdf(self.source / "curriculum.pdf", "Major 7480201. Training curriculum and learning outcomes.")
        self.dataset_id = Ingestor(self.data).ingest(self.source)["dataset_id"]

    def build(self, strategy="recursive", dataset_id=None):
        return self.manager.build(dataset_id or self.dataset_id, strategy, CONTRACT)["bundle_id"]

    def update_checksum(self, bundle_id, filename):
        directory = self.manager.path(bundle_id)
        manifest = read_json(directory / "manifest.json")
        manifest["checksums"][filename] = digest(directory / filename)
        write_json(directory / "manifest.json", manifest)

    def test_build_reload_preserves_equal_id_sets_and_is_idempotent(self):
        identifier = self.build()
        self.assertEqual(self.build(), identifier)
        bundle = self.manager.load(identifier)
        ids = [c["chunk_id"] for c in bundle.chunks]
        self.assertEqual(ids, read_json(self.manager.path(identifier) / "bm25.json")["ids"])
        self.assertEqual(ids, read_json(self.manager.path(identifier) / "vector_ids.json"))
        independent = BundleManager(self.config, embedder_factory=lambda contract: FakeEncoder())
        self.assertEqual(independent.load(identifier).bundle_id, identifier)

    def test_unpublished_bundle_cannot_be_resolved_for_queries(self):
        identifier = self.build()
        with self.assertRaisesRegex(ValueError, "công bố"):
            self.manager.resolve("recursive", identifier)
        with self.assertRaises(FileNotFoundError):
            self.manager.resolve("recursive")
        self.manager.publish(identifier)
        self.assertEqual(self.manager.resolve("recursive").bundle_id, identifier)

    def test_strategy_and_explicit_version_must_match(self):
        identifier = self.build("fixed")
        self.manager.publish(identifier)
        with self.assertRaisesRegex(ValueError, "không tương thích"):
            self.manager.resolve("recursive", identifier)

    def test_release_conflict_keeps_current_bundle(self):
        first = self.build()
        self.manager.publish(first)
        make_pdf(self.source / "second.pdf", "A second document introduces another training version.")
        dataset_id = Ingestor(self.data).ingest(self.source)["dataset_id"]
        second = self.build(dataset_id=dataset_id)
        with self.assertRaisesRegex(ValueError, "đã đổi"):
            self.manager.publish(second, expected_current=None)
        self.assertEqual(self.manager.current(), {"recursive": first})
        self.assertFalse(self.manager.state.published(second))

    def test_request_handle_remains_old_after_publish_and_rollback(self):
        first = self.build()
        self.manager.publish(first)
        handle = self.manager.resolve("recursive")
        old_ids = [c["chunk_id"] for c in handle.chunks]
        make_pdf(self.source / "second.pdf", "Additional outcomes in a distinct snapshot.")
        dataset_id = Ingestor(self.data).ingest(self.source)["dataset_id"]
        second = self.build(dataset_id=dataset_id)
        self.manager.publish(second, expected_current=first)
        self.assertEqual(self.manager.resolve("recursive").bundle_id, second)
        self.assertEqual(handle.bundle_id, first)
        self.assertEqual([c["chunk_id"] for c in handle.chunks], old_ids)
        self.manager.publish(first, expected_current=second, rollback=True)
        self.assertEqual(self.manager.resolve("recursive").bundle_id, first)
        self.assertEqual(self.manager.state.releases()[0]["action"], "rollback")

    def test_rollback_cannot_publish_a_never_published_bundle(self):
        first = self.build()
        self.manager.publish(first)
        make_pdf(self.source / "second.pdf", "Additional outcomes in a distinct snapshot.")
        dataset_id = Ingestor(self.data).ingest(self.source)["dataset_id"]
        second = self.build(dataset_id=dataset_id)
        with self.assertRaisesRegex(ValueError, "từng được phát hành"):
            self.manager.publish(second, expected_current=first, rollback=True)
        self.assertEqual(self.manager.current()["recursive"], first)

    def test_file_checksum_tamper_is_rejected_even_after_cached_load(self):
        identifier = self.build()
        path = self.manager.path(identifier) / "chunks.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Checksum"):
            self.manager.load(identifier)

    def test_structural_id_mismatch_is_rejected(self):
        identifier = self.build()
        path = self.manager.path(identifier) / "vector_ids.json"
        write_json(path, ["orphan_id"])
        self.update_checksum(identifier, "vector_ids.json")
        with self.assertRaisesRegex(ValueError, "ID"):
            self.manager.load(identifier)

    def test_vector_dimension_mismatch_is_rejected(self):
        identifier = self.build()
        path = self.manager.path(identifier) / "vectors.npy"
        values = np.load(path, allow_pickle=False)
        np.save(path, values[:, :7], allow_pickle=False)
        self.update_checksum(identifier, "vectors.npy")
        with self.assertRaisesRegex(ValueError, "vector sai"):
            self.manager.load(identifier)

    def test_nonfinite_vector_is_rejected(self):
        identifier = self.build()
        path = self.manager.path(identifier) / "vectors.npy"
        values = np.load(path, allow_pickle=False)
        values[0, 0] = np.nan
        np.save(path, values, allow_pickle=False)
        self.update_checksum(identifier, "vectors.npy")
        with self.assertRaisesRegex(ValueError, "vector sai"):
            self.manager.load(identifier)

    def test_corrupt_release_preserves_previous_current(self):
        first = self.build()
        self.manager.publish(first)
        make_pdf(self.source / "second.pdf", "Additional independent curriculum content.")
        second = self.build(dataset_id=Ingestor(self.data).ingest(self.source)["dataset_id"])
        path = self.manager.path(second) / "index.faiss"
        path.write_bytes(b"broken index")
        with self.assertRaisesRegex(ValueError, "Checksum"):
            self.manager.publish(second, expected_current=first)
        self.assertEqual(self.manager.current()["recursive"], first)
        self.assertFalse(self.manager.state.published(second))

    def test_unknown_schema_is_rejected(self):
        identifier = self.build()
        path = self.manager.path(identifier) / "manifest.json"
        manifest = read_json(path)
        manifest["schema_version"] = 999
        write_json(path, manifest)
        with self.assertRaisesRegex(ValueError, "schema/QA"):
            self.manager.load(identifier)

    def test_orphan_evidence_is_rejected(self):
        identifier = self.build()
        path = self.manager.path(identifier) / "chunks.json"
        chunks = read_json(path)
        chunks[0]["source_spans"][0]["evidence_id"] = "not_in_dataset"
        write_json(path, chunks)
        self.update_checksum(identifier, "chunks.json")
        with self.assertRaisesRegex(ValueError, "Evidence"):
            self.manager.load(identifier)

    def test_chunk_text_must_match_cited_page_span(self):
        identifier = self.build()
        directory = self.manager.path(identifier)
        chunks = read_json(directory / "chunks.json")
        chunks[0]["text"] = "Unrelated fabricated training requirement."
        write_json(directory / "chunks.json", chunks)
        keywords = read_json(directory / "bm25.json")
        keywords["tokens"][0] = tokenize(chunks[0]["text"])
        write_json(directory / "bm25.json", keywords)
        self.update_checksum(identifier, "chunks.json")
        self.update_checksum(identifier, "bm25.json")
        with self.assertRaises(ValueError):
            self.manager.load(identifier)

    def test_required_component_checksum_cannot_be_omitted(self):
        identifier = self.build()
        path = self.manager.path(identifier) / "manifest.json"
        manifest = read_json(path)
        del manifest["checksums"]["chunks.json"]
        write_json(path, manifest)
        with self.assertRaises(ValueError):
            self.manager.load(identifier)

    def test_source_checksum_tamper_is_rejected(self):
        identifier = self.build()
        document = self.manager.load(identifier).dataset["documents"][0]
        snapshot = self.manager.safe_source(document["reference_path"])
        snapshot.write_bytes(b"replaced original source")
        with self.assertRaisesRegex(ValueError, "Checksum nguồn"):
            self.manager.load(identifier)

    def test_source_and_identifier_paths_cannot_escape_data_root(self):
        outside = self.root / "private.pdf"
        outside.write_bytes(b"private")
        with self.assertRaises(ValueError):
            self.manager.safe_source("../private.pdf")
        with self.assertRaises(ValueError):
            self.manager.safe_source(str(outside))
        with self.assertRaises(ValueError):
            self.manager.path("../outside")


if __name__ == "__main__":
    unittest.main()
