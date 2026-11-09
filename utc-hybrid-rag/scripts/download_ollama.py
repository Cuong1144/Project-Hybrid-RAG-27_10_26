"""Install the official portable Ollama release inside this project."""
import hashlib
import argparse
import io
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / ".tools" / "ollama"
target.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen("https://api.github.com/repos/ollama/ollama/releases/latest", timeout=30) as response:
    release = json.load(response)
asset = next(a for a in release["assets"] if a["name"] == "ollama-windows-amd64.zip")
archive = ROOT / ".cache" / asset["name"]
archive.parent.mkdir(exist_ok=True)
parser = argparse.ArgumentParser()
parser.add_argument("--full", action="store_true", help="Include large CUDA libraries; default CPU/Vulkan portable runtime")
args = parser.parse_args()


class Segment(io.BytesIO):
    def __init__(self, data, start, total):
        super().__init__(data)
        self.start, self.total = start, total

    def seek(self, offset, whence=0):
        if whence == 2:
            offset += self.total
        elif whence == 1:
            offset += self.tell()
        if offset < self.start:
            raise ValueError("Read outside downloaded ZIP segment")
        return super().seek(offset - self.start) + self.start

    def tell(self):
        return super().tell() + self.start


def fetch_range(start, end):
    request = urllib.request.Request(asset["browser_download_url"], headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{asset['size']}":
            raise ValueError("Server did not honor exact ZIP range")
        return response.read()


checksum = None
if args.full:
    if not archive.exists() or archive.stat().st_size != asset["size"]:
        print("Downloading full runtime", release["tag_name"], asset["size"], "bytes", flush=True)
        urllib.request.urlretrieve(asset["browser_download_url"], archive)
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if asset.get("digest") and asset["digest"] != "sha256:" + checksum:
        raise ValueError("Official release digest does not match")
    source = archive
else:
    end = asset["size"] - 1
    tail_start = asset["size"] - 131072
    tail = fetch_range(tail_start, end)
    with zipfile.ZipFile(Segment(tail, tail_start, asset["size"])) as catalog:
        selected = [i for i in catalog.infolist() if not i.is_dir() and "/cuda_" not in i.filename]
        # CPU/Vulkan files are contiguous at the end of official Windows ZIP.
        start = min(i.header_offset for i in selected if i.filename.endswith((".dll", ".exe")))
    print("Downloading CPU/Vulkan runtime", release["tag_name"], asset["size"] - start, "bytes", flush=True)
    segment = fetch_range(start, end)
    source = Segment(segment, start, asset["size"])
with zipfile.ZipFile(source) as zipped:
    for entry in zipped.infolist():
        destination = (target / entry.filename).resolve()
        if not destination.is_relative_to(target.resolve()):
            raise ValueError("Unsafe archive entry")
    for entry in zipped.infolist():
        if args.full or (entry.header_offset >= start and "/cuda_" not in entry.filename):
            zipped.extract(entry, target)
(target / "release.json").write_text(json.dumps({"version": release["tag_name"], "asset": asset["browser_download_url"],
    "archive_sha256_verified": checksum, "installation": "full" if args.full else "cpu_vulkan",
    "file_sha256": {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in target.rglob("*") if p.is_file() and p.name != "release.json"}}, indent=2), encoding="utf-8")
print("Installed", target, flush=True)
