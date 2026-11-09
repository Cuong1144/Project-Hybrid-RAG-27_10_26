import hashlib
from pathlib import Path
import sys
import time
import zlib

import pytest

from utc_rag import archives


PDF = b"%PDF-1.7\n%%EOF\n"


def listing(*entries, details="RAR 5"):
    return ("\r\nUNRAR 6.20 x64 freeware\r\nDetails: " + details + "\r\n\r\n" + "\r\n\r\n".join(
        "\r\n".join(f"        {key}: {value}" for key, value in entry.items()) for entry in entries
    ) + "\r\n").encode("utf-8")


def file_entry(name, content=PDF, **other):
    return {"Name": name, "Type": "File", "Size": len(content), "CRC32": f"{zlib.crc32(content):08X}", **other}


def setup_archive(tmp_path, monkeypatch, contents, streams):
    archive = tmp_path / "source.rar"
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00fake fixture")
    commands = []
    monkeypatch.setattr(archives, "_find_unrar", lambda executable: Path("unrar"))

    def read_command(args, limit, deadline):
        commands.append(args)
        if args[1] == "lt":
            return contents
        assert args[1] == "p" and "-inul" in args and "-@" in args
        assert args[-2] == str(archive.resolve())
        return streams[args[-1]]

    monkeypatch.setattr(archives, "_read_command", read_command)
    return archive, commands


def test_staged_pdf_keeps_unicode_name_and_safe_generated_path(tmp_path, monkeypatch):
    name = r"Tài liệu\Kỹ năng.pdf"
    archive, commands = setup_archive(tmp_path, monkeypatch, listing(file_entry(name)), {name: PDF})
    staging = tmp_path / "staging"
    first = archives.unpack_rar(archive, staging)
    entry = first["members"][0]
    destination = Path(entry["path"])
    assert entry["status"] == "staged"
    assert first["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert first["extractor_version"] == "6.20"
    assert entry["name"] == name and entry["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert destination == staging / f"{entry['member_id']}.pdf"
    assert destination.read_bytes() == PDF
    assert commands[-1][-1] == name
    mtime = destination.stat().st_mtime_ns
    assert archives.unpack_rar(archive, staging) == first
    assert destination.stat().st_mtime_ns == mtime
    destination.write_bytes(b"tampered")
    assert archives.unpack_rar(archive, staging) == first
    assert destination.read_bytes() == PDF


def test_unsafe_or_ambiguous_entries_are_inventoried_without_streaming(tmp_path, monkeypatch):
    entries = [file_entry(name) for name in (
        r"..\escape.pdf", r"C:\drive.pdf", r"\absolute.pdf", r"x\name:stream.pdf", "CON.pdf",
        r"x\*.pdf", r"x\a.\note.pdf", r"dir\Same.pdf", "dir/same.pdf", "NUL .pdf",
    )]
    entries += [file_entry("link.pdf", Type="Unix symlink"), file_entry("reparse.pdf", Target="D:\\other.pdf"),
                file_entry("encrypted.pdf", Flags="encrypted"), file_entry("notes.txt"),
                {"Name": "dir", "Type": "Directory"}]
    archive, commands = setup_archive(tmp_path, monkeypatch, listing(*entries), {})
    result = archives.unpack_rar(archive, tmp_path / "staging")
    assert len(commands) == 1
    assert len(result["members"]) == len(entries)
    assert len({row["member_id"] for row in result["members"]}) == len(entries)
    assert {row["name"] for row in result["members"] if row["status"] == "skipped"} == {"dir", "notes.txt"}
    assert all("path" not in row and row.get("reason") for row in result["members"])


@pytest.mark.parametrize("bytes_streamed", [b"%PDF-1.7\n%%EOX\n", PDF + b"extra", b"plain text only"])
def test_corrupt_or_wrong_stream_never_becomes_staged(tmp_path, monkeypatch, bytes_streamed):
    archive, _ = setup_archive(tmp_path, monkeypatch, listing(file_entry("expected.pdf")), {"expected.pdf": bytes_streamed})
    stage = tmp_path / "staging"
    entry = archives.unpack_rar(archive, stage)["members"][0]
    assert entry["status"] == "blocked" and "path" not in entry
    assert list(stage.iterdir()) == []


def test_declared_size_limit_and_multivolume(tmp_path, monkeypatch):
    archive, commands = setup_archive(tmp_path, monkeypatch, listing(file_entry("large.pdf", Size=1 << 40)), {})
    stage = tmp_path / "staging"
    row = archives.unpack_rar(archive, stage)["members"][0]
    assert row["status"] == "blocked" and len(commands) == 1
    with pytest.raises(archives.ArchiveError, match="nhiều phần"):
        archives._parse_listing(listing(file_entry("one.pdf"), details="RAR 5, volume 1"))


def test_stdout_limit_applies_before_process_finishes():
    command = [sys.executable, "-c", "import sys,time;sys.stdout.buffer.write(b'a'*256);sys.stdout.flush();time.sleep(10)"]
    start = time.monotonic()
    with pytest.raises(archives.ArchiveError, match="kích thước"):
        archives._read_command(command, 128, start + 5)
    assert time.monotonic() - start < 5


def test_archive_command_timeout_unblocks_a_pipe_read():
    command = [sys.executable, "-c", "import time;time.sleep(10)"]
    start = time.monotonic()
    with pytest.raises(archives.ArchiveError, match="thời gian"):
        archives._read_command(command, 128, start + 0.5)
    assert time.monotonic() - start < 5
