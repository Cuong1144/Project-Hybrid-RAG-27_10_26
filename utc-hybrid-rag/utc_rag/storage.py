"""JSON snapshots plus transactional operational state; no serialized executable objects."""
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


class State:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "operations.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS current_bundles(strategy TEXT PRIMARY KEY, bundle_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS releases(id INTEGER PRIMARY KEY, created_at TEXT, actor TEXT,
                    action TEXT, strategy TEXT, bundle_id TEXT, previous_id TEXT);
                CREATE TABLE IF NOT EXISTS requests(idempotency_key TEXT PRIMARY KEY, job_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def current(self):
        with self.connect() as db:
            return dict(db.execute("SELECT strategy,bundle_id FROM current_bundles"))

    def releases(self):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM releases ORDER BY id DESC")]

    def published(self, bundle_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM releases WHERE bundle_id=? LIMIT 1", (bundle_id,)).fetchone() is not None

    def switch(self, strategy, bundle_id, expected_current, action="publish", actor="operator"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT bundle_id FROM current_bundles WHERE strategy=?", (strategy,)).fetchone()
            old = row[0] if row else None
            if old != expected_current:
                raise ValueError("Phiên bản hiện hành đã đổi. Hãy tải lại danh sách trước khi phát hành.")
            if old == bundle_id:
                return old
            if action == "rollback" and not db.execute("SELECT 1 FROM releases WHERE bundle_id=?", (bundle_id,)).fetchone():
                raise ValueError("Chỉ quay lại bundle từng được phát hành.")
            db.execute("INSERT OR REPLACE INTO current_bundles VALUES (?,?)", (strategy, bundle_id))
            db.execute("INSERT INTO releases(created_at,actor,action,strategy,bundle_id,previous_id) VALUES (?,?,?,?,?,?)",
                       (now(), actor, action, strategy, bundle_id, old))
            return old

    def save_job(self, job):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?)", (job["job_id"], json.dumps(job, ensure_ascii=False)))

    def jobs(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM jobs ORDER BY rowid DESC")]
