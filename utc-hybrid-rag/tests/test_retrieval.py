"""Observable retrieval contracts, independent of a downloaded embedding model."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from utc_rag.retrieval import RetrievalIncomplete, make_bm25, metadata_value, retrieve, rrf, tokenize


class QueryEncoder:
    def encode(self, texts, query=False):
        return np.asarray([[1.0, 0.0] for _ in texts], dtype="float32")


def scoped_chunk(identifier, scope, status="verified", text="training curriculum"):
    return {
        "chunk_id": identifier, "text": text,
        "source_spans": [{"evidence_id": "ev_" + identifier, "file_id": "source", "pdf_page": 1}],
        "metadata": {"major_code": {"value": scope, "verification_status": status,
                                      "evidence_ids": ["ev_" + identifier]}},
    }


def bundle_for(chunks, vectors):
    return SimpleNamespace(chunks=chunks, vectors=np.asarray(vectors, dtype="float32"),
                           bm25=make_bm25([tokenize(c["text"]) for c in chunks]),
                           embedder=QueryEncoder())


class RetrievalTests(unittest.TestCase):
    def test_rrf_uses_ranks_and_deduplicates_each_branch(self):
        result = rrf({"bm25": [{"chunk_id": "a", "score": 500},
                                  {"chunk_id": "a", "score": 400},
                                  {"chunk_id": "b", "score": 300}],
                      "faiss": [{"chunk_id": "b", "score": 0.9},
                                {"chunk_id": "c", "score": 0.8}]})
        by_id = {r["chunk_id"]: r for r in result}
        self.assertEqual([r["chunk_id"] for r in result], ["b", "a", "c"])
        self.assertAlmostEqual(by_id["a"]["rrf_score"], 1 / 61)
        self.assertAlmostEqual(by_id["b"]["rrf_score"], 1 / 63 + 1 / 61)
        self.assertEqual(by_id["b"]["bm25_rank"], 3)
        self.assertIsNone(by_id["c"]["bm25_rank"])

    def test_rrf_ties_have_deterministic_id_order(self):
        result = rrf({"bm25": [{"chunk_id": "z", "score": 1}],
                      "faiss": [{"chunk_id": "a", "score": 1}]})
        self.assertEqual([r["chunk_id"] for r in result], ["a", "z"])

    def test_scope_is_applied_before_both_top_eight_lists(self):
        chunks = [scoped_chunk("wrong_%02d" % i, "other", text="needle exact") for i in range(12)]
        chunks += [scoped_chunk("wanted", "7480201", text="needle relevant curriculum")]
        vectors = [[1.0, 0.0] for _ in range(12)] + [[0.2, np.sqrt(0.96)]]
        result = retrieve(bundle_for(chunks, vectors), "needle", {"major_code": "7480201"})
        self.assertEqual(result["eligible_count"], 1)
        self.assertEqual([r["chunk_id"] for r in result["faiss"]], ["wanted"])
        self.assertEqual([r["chunk_id"] for r in result["bm25"]], ["wanted"])
        self.assertEqual([r["chunk_id"] for r in result["results"]], ["wanted"])

    def test_unverified_metadata_cannot_satisfy_filter(self):
        chunks = [scoped_chunk("unreviewed", "7480201", "unverified"),
                  scoped_chunk("reviewed", "7480201")]
        result = retrieve(bundle_for(chunks, [[1, 0], [0, 1]]), "training", {"major_code": "7480201"})
        self.assertEqual([r["chunk_id"] for r in result["results"]], ["reviewed"])
        self.assertIsNone(metadata_value(chunks[0]["metadata"], "major_code"))

    def test_verified_metadata_without_evidence_cannot_satisfy_filter(self):
        self.assertIsNone(metadata_value({"major_code": {"value": "7480201", "verification_status": "verified",
                                                         "evidence_ids": []}}, "major_code"))

    def test_no_lexical_match_is_empty_branch_not_padded_hits(self):
        chunks = [scoped_chunk("a", "7480201", text="curriculum education")]
        result = retrieve(bundle_for(chunks, [[1, 0]]), "unfindabletoken")
        self.assertEqual(result["bm25"], [])
        self.assertEqual(len(result["faiss"]), 1)
        self.assertIsNone(result["results"][0]["bm25_rank"])

    def test_empty_scope_returns_no_hits_without_embedding_call(self):
        bundle = bundle_for([scoped_chunk("a", "other")], [[1, 0]])
        class MustNotEncode:
            def encode(self, *_args, **_kwargs):
                raise AssertionError("No eligible IDs should not trigger embedding")
        bundle.embedder = MustNotEncode()
        result = retrieve(bundle, "training", {"major_code": "7480201"})
        self.assertEqual(result, {"results": [], "bm25": [], "faiss": [], "eligible_count": 0})

    def test_codes_survive_unicode_tokenization(self):
        self.assertEqual(tokenize("QD-123/ĐH 7480201 IT.001"), ["qd-123/đh", "7480201", "it.001"])

    def test_zero_deadline_returns_incomplete_before_embedding(self):
        bundle = bundle_for([scoped_chunk("wanted", "7480201")], [[1, 0]])
        bundle.embedder.encode = Mock(side_effect=AssertionError("Expired retrieval must not encode"))
        with self.assertRaises(RetrievalIncomplete) as caught:
            retrieve(bundle, "training", timeout=0)
        self.assertEqual(caught.exception.phase, "embedding")
        self.assertEqual(caught.exception.partial["eligible_count"], 1)
        self.assertEqual([hit["chunk_id"] for hit in caught.exception.partial["bm25"]], ["wanted"])
        bundle.embedder.encode.assert_not_called()

    def test_embedding_crossing_deadline_stops_before_faiss(self):
        bundle = bundle_for([scoped_chunk("wanted", "7480201")], [[1, 0]])
        bundle.embedder.encode = Mock(return_value=np.asarray([[1, 0]], dtype="float32"))
        with patch("utc_rag.retrieval.time.perf_counter", side_effect=[0.0, 0.5, 2.0]), patch("faiss.IndexFlatIP") as constructor:
            with self.assertRaises(RetrievalIncomplete) as caught:
                retrieve(bundle, "training", timeout=1.0)
        self.assertEqual(caught.exception.phase, "faiss")
        self.assertEqual(caught.exception.partial["eligible_count"], 1)
        bundle.embedder.encode.assert_called_once_with(["training"], query=True)
        constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
