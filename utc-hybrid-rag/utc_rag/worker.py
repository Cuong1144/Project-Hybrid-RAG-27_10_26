"""Run separately: python -m utc_rag worker (one worker per machine)."""
import time
from pathlib import Path

from .ingestion import Ingestor
from .storage import now


def run_one(manager):
    with manager.state.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT job_id,body FROM jobs WHERE json_extract(body,'$.status')='pending' ORDER BY rowid LIMIT 1").fetchone()
        if not row:
            return False
        import json
        job = json.loads(row[1])
        job.update(status="running", started_at=now())
        db.execute("UPDATE jobs SET body=? WHERE job_id=?", (json.dumps(job, ensure_ascii=False), job["job_id"]))
    try:
        if job["kind"] == "ingest":
            output = Ingestor(manager.root).ingest(
                Path(job["source_path"]), Path(job["metadata_path"]) if job.get("metadata_path") else None,
                ocr=job.get("ocr", False), expand_archives=job.get("expand_archives", False),
            )
            job["result"] = output
            job["dataset_id"] = output["dataset_id"]
        else:
            output = manager.build(job["dataset_id"], job["strategy"])
            job["bundle_id"] = output["bundle_id"]
        job.update(status="completed", finished_at=now())
    except Exception as exc:
        job.update(status="failed", error=type(exc).__name__ + ": " + str(exc), finished_at=now())
    manager.state.save_job(job)
    return True


def work(manager, once=False):
    # Interrupted jobs can be explicitly resumed using `resume-jobs`; never
    # assume another running worker has disappeared simply because this starts.
    while True:
        ran = run_one(manager)
        if once:
            return
        if not ran:
            time.sleep(1)
