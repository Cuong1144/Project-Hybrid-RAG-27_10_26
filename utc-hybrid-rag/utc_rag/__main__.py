import argparse
import json
from pathlib import Path

from .bundles import BundleManager
from .config import settings
from .storage import write_json


def main():
    parser = argparse.ArgumentParser(description="UTC Hybrid RAG local tools")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("source", type=Path)
    ingest.add_argument("--metadata", type=Path)
    ingest.add_argument("--ocr", action="store_true", help="Stage Vietnamese/English OCR candidates for review")
    ingest.add_argument("--expand-archives", action="store_true", help="Stage supported members of local RAR files")
    review = commands.add_parser("ocr-review", help="Export candidate text, PDF locators and approval checksums")
    review.add_argument("dataset_id")
    review.add_argument("--output", type=Path, default=Path("reports/ocr"))
    build = commands.add_parser("build")
    build.add_argument("dataset_id")
    build.add_argument("--strategy", choices=["fixed", "recursive", "both"], default="both")
    publish = commands.add_parser("publish")
    publish.add_argument("bundle_id")
    publish.add_argument("--expected-current", default=None)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("bundle_id")
    rollback.add_argument("--expected-current", required=True)
    commands.add_parser("versions")
    commands.add_parser("models")
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    commands.add_parser("resume-jobs")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("questions", type=Path)
    evaluate.add_argument("--output", type=Path, default=Path("reports/evaluation"))
    save = commands.add_parser("backup")
    save.add_argument("destination", type=Path)
    load = commands.add_parser("restore")
    load.add_argument("archive", type=Path)
    load.add_argument("target", type=Path)
    args = parser.parse_args()
    manager = BundleManager(settings)
    result = None
    if args.command == "ingest":
        from .ingestion import Ingestor
        result = Ingestor(settings.data_dir).ingest(args.source, args.metadata, ocr=args.ocr,
                                                   expand_archives=args.expand_archives)
        manager.state.save_job(result)
    elif args.command == "ocr-review":
        from .ocr_review import export_review
        from .storage import read_json
        result = export_review(read_json(manager.path(args.dataset_id, "datasets") / "dataset.json"),
                               manager.root, args.output)
    elif args.command == "build":
        strategies = ["fixed", "recursive"] if args.strategy == "both" else [args.strategy]
        result = [manager.build(args.dataset_id, strategy) for strategy in strategies]
    elif args.command in ("publish", "rollback"):
        result = manager.publish(args.bundle_id, args.expected_current, args.command == "rollback")
    elif args.command == "versions":
        result = manager.versions()
    elif args.command == "models":
        from .retrieval import Embedder
        from .generation import download_tokenizer
        contract = Embedder.download(settings.embedding_model, settings.embedding_revision, settings.model_cache)
        write_json(settings.data_dir / "embedding_contract.json", contract)
        result = {"embedding": contract, "generation": download_tokenizer(settings)}
    elif args.command == "worker":
        from .worker import work
        work(manager, args.once)
    elif args.command == "resume-jobs":
        count = 0
        for job in manager.state.jobs():
            if job.get("status") in ("running", "failed") and job.get("kind") in ("ingest", "build"):
                job["status"] = "pending"
                manager.state.save_job(job)
                count += 1
        result = {"requeued": count}
    elif args.command == "serve":
        import uvicorn
        uvicorn.run("utc_rag.api:app", host=args.host, port=args.port)
    elif args.command == "evaluate":
        from .evaluation import evaluate
        report = evaluate(manager, args.questions, args.output)
        result = {"summary": report["summary"], "output": str(args.output)}
    elif args.command == "backup":
        from .backup import backup
        result = backup(manager, args.destination)
    elif args.command == "restore":
        from .backup import restore
        result = restore(args.archive, args.target, settings)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
