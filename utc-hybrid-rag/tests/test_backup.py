"""Backup/restore validates durable state, index pairs and source snapshots."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from test_bundles import CONTRACT, FakeEncoder, make_pdf
from utc_rag.backup import backup, restore
from utc_rag.bundles import BundleManager
from utc_rag.config import Settings
from utc_rag.ingestion import Ingestor
from utc_rag.retrieval import retrieve
from utc_rag.storage import digest, read_json, write_json


class BackupTests(unittest.TestCase):
    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / ".cache" / "test-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="backup_", dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.data = self.root / "sources", self.root / "data"
        self.configuration = Settings(data_dir=self.data, source_root=self.source,
                                      model_cache=self.root / "model-cache", embedding_model=CONTRACT["model"])
        self.manager = BundleManager(self.configuration, embedder_factory=lambda contract: FakeEncoder())
        make_pdf(self.source / "training.pdf", "Major 7480201. Training curriculum and learning outcomes.")
        first_dataset = Ingestor(self.data).ingest(self.source)["dataset_id"]
        self.first = self.manager.build(first_dataset, "recursive", CONTRACT)["bundle_id"]
        self.fixed = self.manager.build(first_dataset, "fixed", CONTRACT)["bundle_id"]
        self.manager.publish(self.first)
        self.manager.publish(self.fixed)
        make_pdf(self.source / "additional.pdf", "A later source includes additional graduation requirements.")
        second_dataset = Ingestor(self.data).ingest(self.source)["dataset_id"]
        self.second = self.manager.build(second_dataset, "recursive", CONTRACT)["bundle_id"]
        self.manager.publish(self.second, expected_current=self.first)
        self.manager.publish(self.first, expected_current=self.second, rollback=True)
        self.archive = self.root / "exports" / "utc-backup.zip"
        self.target = self.root / "restored"
        write_json(self.data / "embedding_contract.json", CONTRACT)
        write_json(self.data / "traces" / "request1.json", {"request_id": "request1", "status": "partial"})
        self.manager.state.save_job({"job_id": "completed_fixture", "kind": "ingest", "status": "completed"})

    def restored_manager(self):
        return BundleManager(replace(self.configuration, data_dir=self.target))

    def test_zip_restore_preserves_all_versions_release_history_and_sources(self):
        original_current = self.manager.current()
        original_history = self.manager.state.releases()
        result = backup(self.manager, self.archive)
        self.assertEqual(result["backup"], str(self.archive.resolve()))
        self.assertGreater(result["bytes"], 0)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertIn("operations.sqlite3", archive.namelist())
            self.assertIn("embedding_contract.json", archive.namelist())
            self.assertTrue(any(name.startswith("snapshots/") and name.endswith(".pdf") for name in archive.namelist()))
        # The restore API constructs a manager normally. Only the embedding
        # boundary is injected; actual FAISS, SQLite, manifests and PDFs remain.
        with patch("utc_rag.bundles.Embedder", side_effect=lambda contract, cache: FakeEncoder()):
            restored = restore(self.archive, self.target, self.configuration)
            manager = self.restored_manager()
            self.assertEqual(set(restored["verified_bundles"]), {self.first, self.fixed, self.second})
            self.assertEqual(restored["current"], original_current)
            self.assertEqual(manager.current(), original_current)
            self.assertEqual(manager.state.releases(), original_history)
            self.assertEqual(manager.state.jobs()[0]["job_id"], "completed_fixture")
            for identifier in (self.first, self.fixed, self.second):
                bundle = manager.load(identifier)
                self.assertTrue(manager.state.published(identifier))
                self.assertTrue(retrieve(bundle, "training requirements", {}, 3)["results"])
                for document in bundle.dataset["documents"]:
                    if document.get("reference_path"):
                        source = manager.safe_source(document["reference_path"])
                        self.assertTrue(source.read_bytes().startswith(b"%PDF"))
                        self.assertEqual(digest(source), document["sha256"])
            self.assertEqual(read_json(self.target / "embedding_contract.json"), CONTRACT)
            self.assertEqual(read_json(self.target / "traces" / "request1.json")["status"], "partial")

    def test_existing_empty_target_is_allowed(self):
        backup(self.manager, self.archive)
        self.target.mkdir()
        with patch("utc_rag.bundles.Embedder", side_effect=lambda contract, cache: FakeEncoder()):
            result = restore(self.archive, self.target, self.configuration)
        self.assertEqual(result["current"], self.manager.current())

    def test_nonempty_target_is_not_overwritten(self):
        backup(self.manager, self.archive)
        self.target.mkdir()
        marker = self.target / "keep.txt"
        marker.write_text("preserve this user file", encoding="utf-8")
        with self.assertRaises(ValueError):
            restore(self.archive, self.target, self.configuration)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve this user file")
        self.assertEqual([p.name for p in self.target.iterdir()], ["keep.txt"])

    def test_backup_must_be_outside_data_tree_and_omits_temporary_files(self):
        inside = self.data / "backup.zip"
        with self.assertRaises(ValueError):
            backup(self.manager, inside)
        self.assertFalse(inside.exists())
        (self.data / "unfinished.tmp").write_text("incomplete", encoding="utf-8")
        backup(self.manager, self.archive)
        with zipfile.ZipFile(self.archive) as archive:
            self.assertNotIn("unfinished.tmp", archive.namelist())

    def test_zip_traversal_is_refused_before_any_member_is_extracted(self):
        malicious = self.root / "malicious.zip"
        for index, entry in enumerate(("../escape.txt", "nested/../../escape.txt", str(self.root / "absolute.txt"))):
            target = self.root / ("malicious_target_" + str(index))
            with zipfile.ZipFile(malicious, "w") as archive:
                archive.writestr("safe.txt", "a valid member before malicious entry")
                archive.writestr(entry, "must never be written")
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                restore(malicious, target, self.configuration)
            self.assertFalse((self.root / "escape.txt").exists())
            self.assertFalse((self.root / "absolute.txt").exists())
            self.assertFalse((target / "safe.txt").exists())

    def test_restore_refuses_corrupt_snapshot_in_archive(self):
        backup(self.manager, self.archive)
        corrupt = self.root / "corrupt.zip"
        changed = False
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(corrupt, "w") as target:
            for entry in source.infolist():
                value = source.read(entry.filename)
                if not changed and entry.filename.startswith("snapshots/") and entry.filename.endswith(".pdf"):
                    value = b"corrupted source snapshot"
                    changed = True
                target.writestr(entry, value)
        self.assertTrue(changed)
        with patch("utc_rag.bundles.Embedder", side_effect=lambda contract, cache: FakeEncoder()):
            with self.assertRaisesRegex(ValueError, "Checksum"):
                restore(corrupt, self.target, self.configuration)


if __name__ == "__main__":
    unittest.main()
