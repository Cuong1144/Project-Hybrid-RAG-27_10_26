import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from utc_rag.evaluation import covers_evidence, evaluate, score_query


def chunk(chunk_id, text="Chuẩn đầu ra công nghệ thông tin", page=1, start=0, end=100):
    return {"chunk_id": chunk_id, "text": text,
            "source_spans": [{"file_id": "f1", "pdf_page": page,
                              "char_start": start, "char_end": end}]}


class EvaluationTests(unittest.TestCase):
    def test_precision_uses_three_and_recall_uses_strategy_relevant_chunks(self):
        chunks = [chunk("a"), chunk("b"), chunk("c", page=2)]
        gold = [{"file_id": "f1", "pdf_page": 1, "char_start": 10, "char_end": 20}]
        result = score_query(chunks, [chunks[0]], gold)
        self.assertEqual(result["precision_at_3"], 1 / 3)
        self.assertEqual(result["recall_at_3"], 1 / 2)
        self.assertEqual(result["evidence_coverage_at_3"], 1)

    def test_overlapping_chunks_and_duplicate_gold_do_not_double_count_evidence(self):
        chunks = [chunk("a"), chunk("b")]
        unit = {"file_id": "f1", "pdf_page": 1, "quote": "Chuẩn đầu ra"}
        result = score_query(chunks, chunks, [unit, dict(unit)])
        self.assertEqual(result["gold_evidence_count"], 1)
        self.assertEqual(result["evidence_coverage_at_3"], 1)
        self.assertEqual(result["precision_at_3"], 2 / 3)

    def test_partial_character_overlap_is_not_full_coverage(self):
        gold = {"file_id": "f1", "pdf_page": 1, "char_start": 90, "char_end": 110}
        self.assertFalse(covers_evidence(chunk("a", end=100), gold))

    def test_wrong_source_or_page_never_matches_a_quote(self):
        gold = {"file_id": "f1", "pdf_page": 2, "quote": "Chuẩn đầu ra"}
        self.assertFalse(covers_evidence(chunk("a"), gold))
        gold.update(file_id="other", pdf_page=1)
        self.assertFalse(covers_evidence(chunk("a"), gold))

    def test_multipage_quote_requires_span_text(self):
        item = chunk("a")
        item["source_spans"].append({"file_id": "f1", "pdf_page": 2})
        gold = {"file_id": "f1", "pdf_page": 2, "quote": "Chuẩn đầu ra"}
        self.assertFalse(covers_evidence(item, gold))
        item["source_spans"][1]["text"] = "Chuẩn đầu ra"
        self.assertTrue(covers_evidence(item, gold))

    def test_quote_normalizes_whitespace_without_fuzzy_matching(self):
        gold = {"file_id": "f1", "pdf_page": 1, "quote": "Chuẩn\nđầu ra"}
        self.assertTrue(covers_evidence(chunk("a"), gold))
        gold["quote"] = "chuan dau ra"
        self.assertFalse(covers_evidence(chunk("a"), gold))

    def test_no_gold_recall_is_undefined_and_unknown_result_is_rejected(self):
        self.assertIsNone(score_query([chunk("a")], [], [])["recall_at_3"])
        with self.assertRaisesRegex(ValueError, "unknown chunk"):
            score_query([chunk("a")], [chunk("missing")], [])

    def _manager(self, recursive_dataset="d1", recursive_embedding=None):
        embedding = {"model": "pilot", "revision": "r1", "dimension": 4}
        bundles = {
            "fixed-b": SimpleNamespace(chunks=[chunk("a")], manifest={"dataset_version": "d1", "embedding": embedding}),
            "recursive-b": SimpleNamespace(chunks=[chunk("b")], manifest={"dataset_version": recursive_dataset,
                                           "embedding": recursive_embedding or embedding}),
        }
        return SimpleNamespace(current=lambda: {"fixed": "fixed-b", "recursive": "recursive-b"},
                               load=lambda key: bundles[key])

    def _questions(self, root, evidence=True):
        path = root / "questions.json"
        path.write_text(json.dumps({"dataset_version": "d1", "questions": [
            {"id": "q1", "category": "keyword", "query": "Chuẩn đầu ra",
             "evidence": [{"file_id": "f1", "pdf_page": 1, "quote": "Chuẩn đầu ra"}] if evidence else []}
        ]}), encoding="utf-8")
        return path

    def test_evaluate_exports_conditions_and_does_not_claim_full_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._questions(root)
            fake_retrieval = SimpleNamespace(retrieve=lambda b, *a, **k: {"results": b.chunks})
            with patch.dict("sys.modules", {"utc_rag.retrieval": fake_retrieval}):
                report = evaluate(self._manager(), path, root / "results")
            self.assertFalse(report["minimum_category_counts_met"])
            self.assertFalse(report["conditions"]["answer_quality_evaluated"])
            self.assertEqual(report["summary"]["fixed"]["precision_at_3"], 1 / 3)
            self.assertTrue((root / "results" / "retrieval_metrics.csv").exists())
            saved = json.loads((root / "results" / "retrieval_metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["results"][0]["top3_chunk_ids"], ["a"])

    def test_evaluate_rejects_different_datasets_and_embedding_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._questions(root)
            with self.assertRaisesRegex(ValueError, "dataset_version"):
                evaluate(self._manager(recursive_dataset="d2"), path, root / "out")
            with self.assertRaisesRegex(ValueError, "embedding"):
                evaluate(self._manager(recursive_embedding={"model": "other"}), path, root / "out")

    def test_main_questions_require_gold_and_bad_gold_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._questions(root, evidence=False)
            with self.assertRaisesRegex(ValueError, "gold evidence"):
                evaluate(self._manager(), path, root / "out")
            data = json.loads(path.read_text(encoding="utf-8"))
            data["questions"][0]["evidence"] = [{"file_id": "f1", "pdf_page": 0, "quote": "text"}]
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "pdf_page"):
                evaluate(self._manager(), path, root / "out")


if __name__ == "__main__":
    unittest.main()
