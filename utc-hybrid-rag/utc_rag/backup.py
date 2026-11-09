from pathlib import Path
import sqlite3
import tempfile
import zipfile

from .bundles import BundleManager
from .config import Settings


def backup(manager, destination: Path):
    destination = destination.resolve()
    if destination.is_relative_to(manager.root):
        raise ValueError("Backup phải ở ngoài thư mục data.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        sqlite_snapshot = Path(temp) / "operations.sqlite3"
        with manager.state.connect() as source:
            target = sqlite3.connect(sqlite_snapshot)
            try:
                source.backup(target)
            finally:
                target.close()
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in manager.root.rglob("*"):
                if file.is_file() and file.name not in {"operations.sqlite3", "operations.sqlite3-wal", "operations.sqlite3-shm"} and not file.name.endswith(".tmp"):
                    archive.write(file, file.relative_to(manager.root).as_posix())
            archive.write(sqlite_snapshot, "operations.sqlite3")
    return {"backup": str(destination), "bytes": destination.stat().st_size}


def restore(archive_path: Path, target_dir: Path, configuration: Settings):
    target_dir = target_dir.resolve()
    if target_dir.exists() and any(target_dir.iterdir()):
        raise ValueError("Chỉ restore vào thư mục mới hoặc rỗng.")
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            if not (target_dir / entry.filename).resolve().is_relative_to(target_dir):
                raise ValueError("Archive chứa đường dẫn ngoài phạm vi.")
        archive.extractall(target_dir)
    from dataclasses import replace
    manager = BundleManager(replace(configuration, data_dir=target_dir))
    checked = []
    for item in manager.versions()["versions"]:
        manager.load(item["bundle_id"])
        checked.append(item["bundle_id"])
    from .retrieval import retrieve
    for strategy, bundle_id in manager.current().items():
        bundle = manager.resolve(strategy, bundle_id)
        if not retrieve(bundle, bundle.chunks[0]["text"][:100], {}, 1)["results"]:
            raise ValueError("Restore smoke query thất bại.")
    return {"restored_to": str(target_dir), "verified_bundles": checked, "current": manager.current()}
