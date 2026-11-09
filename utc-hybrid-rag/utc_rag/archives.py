"""Stage regular document members of a RAR without extracting archived paths."""

from collections import Counter
import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import threading
import time
import unicodedata
import uuid
import zlib

from .storage import digest


ARCHIVE_REVISION = "unrar-stream-v1"
_RAR_MAGIC = (b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")
_SUPPORTED = {".pdf", ".doc", ".docx"}
_UNRAR_SWITCHES = ("-cfg-", "-c-", "-p-", "-@", "--")
_MAX_ARCHIVE = 256 * 1024 * 1024
_MAX_MEMBER = 64 * 1024 * 1024
_MAX_TOTAL = 256 * 1024 * 1024
_MAX_LIST = 2 * 1024 * 1024
_MAX_MEMBERS = 1000
_MAX_SECONDS = 90
_DEVICE = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[0-9¹²³]|LPT[0-9¹²³])$", re.I)
_FIELD = re.compile(r"^\s+([A-Za-z0-9 _-]+): (.*)$")


class ArchiveError(ValueError):
    """The archive cannot be inventoried or safely staged."""


def _reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _find_unrar(executable: Path | None) -> Path:
    if executable is not None:
        candidate = Path(executable)
        if candidate.is_file():
            return candidate.resolve()
        raise ArchiveError("Không tìm thấy UnRAR tại đường dẫn đã cấu hình.")
    discovered = shutil.which("unrar")
    if discovered:
        return Path(discovered).resolve()
    if os.name == "nt":
        for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(variable)
            if base and (candidate := Path(base) / "WinRAR" / "UnRAR.exe").is_file():
                return candidate.resolve()
    raise ArchiveError("Chưa cài UnRAR. Cài WinRAR hoặc thêm UnRAR vào PATH.")


def _read_command(args: list[str], limit: int, deadline: float) -> bytes:
    """Read a bounded stdout pipe; kill on deadline even if a read is blocked."""
    timeout = min(30.0, deadline - time.monotonic())
    if timeout <= 0:
        raise ArchiveError("Đã hết ngân sách thời gian giải nén archive.")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, creationflags=flags) as process:
        expired = threading.Event()

        def stop_at_deadline():
            if process.poll() is None:
                expired.set()
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

        watchdog = threading.Timer(timeout, stop_at_deadline)
        watchdog.daemon = True
        watchdog.start()
        output = bytearray()
        try:
            while block := process.stdout.read(min(1024 * 1024, limit + 1 - len(output))):
                output.extend(block)
                if len(output) > limit:
                    raise ArchiveError("Dữ liệu UnRAR vượt kích thước được phép.")
            status = process.wait()
            if expired.is_set():
                raise ArchiveError("UnRAR vượt thời gian xử lý cho phép.")
            if status != 0:
                raise ArchiveError(f"UnRAR dừng với mã {status}; kiểm tra tệp hỏng hoặc có mật khẩu.")
            return bytes(output)
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()


def _parse_listing(listing: bytes) -> list[dict]:
    try:
        text = listing.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArchiveError("UnRAR không trả danh sách tên UTF-8 hợp lệ.") from exc
    records = []
    current = None
    for line in text.split("\n"):
        line = line.removesuffix("\r")
        if line.startswith("Details:") and re.search(r"\bvolume\b", line, re.I):
            raise ArchiveError("Archive nhiều phần chưa được hỗ trợ.")
        field = _FIELD.fullmatch(line)
        if field and field[1] == "Name":
            current = {"Name": field[2]}
            records.append(current)
            if len(records) > _MAX_MEMBERS:
                raise ArchiveError("Archive có quá nhiều mục.")
        elif current is not None:
            if not line.strip():
                current = None
            elif field and field[1] not in current:
                current[field[1]] = field[2]
            else:
                raise ArchiveError("Không đọc được đầy đủ thông tin từng mục trong archive.")
    if not records or any("Type" not in entry for entry in records):
        raise ArchiveError("Danh sách archive rỗng hoặc thiếu kiểu mục.")
    return records


def _safe_name(name: str) -> bool:
    parts = name.replace("/", "\\").split("\\")
    return len(name) <= 1024 and all(
        part and part not in {".", ".."} and part == part.rstrip(". ")
        and not any(ord(ch) < 32 or ord(ch) == 127 or ch in '<>:"|?*' for ch in part)
        and not _DEVICE.fullmatch(part.split(".", 1)[0].rstrip())
        for part in parts
    )


def _normalized_name(name: str) -> str:
    return unicodedata.normalize("NFC", name.replace("/", "\\")).casefold()


def _member_bytes_valid(suffix: str, content: bytes) -> bool:
    signatures = {".pdf": (b"%PDF-",), ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"{\\rtf"),
                  ".docx": (b"PK\x03\x04",)}
    return content.startswith(signatures[suffix])


def unpack_rar(archive_path: Path, staging_dir: Path, *, executable: Path | None = None) -> dict:
    """Return a deterministic inventory plus absolute paths of verified staged members.

    ``staging_dir`` is a private directory selected by the caller. Member names
    are passed as literal selection arguments, and never form destination paths.
    """
    archive_path, staging_dir = Path(archive_path), Path(staging_dir)
    if _reparse(archive_path) or not archive_path.is_file() or archive_path.suffix.casefold() != ".rar":
        raise ArchiveError("Nguồn không phải tệp RAR thông thường.")
    archive_path = archive_path.resolve()
    if archive_path.stat().st_size > _MAX_ARCHIVE:
        raise ArchiveError("Tệp RAR vượt giới hạn kích thước.")
    with archive_path.open("rb") as archive:
        if not archive.read(8).startswith(_RAR_MAGIC):
            raise ArchiveError("Chữ ký tệp RAR không hợp lệ.")
    archive_sha256 = digest(archive_path)
    exe = str(_find_unrar(executable))
    deadline = time.monotonic() + _MAX_SECONDS
    try:
        listing = _read_command([exe, "lt", "-scfr", *_UNRAR_SWITCHES, str(archive_path)], _MAX_LIST, deadline)
    except OSError as exc:
        raise ArchiveError("Không chạy được UnRAR để đọc danh sách archive.") from exc
    entries = _parse_listing(listing)
    multiplicity = Counter(_normalized_name(entry["Name"]) for entry in entries)

    staging_dir = staging_dir.absolute()
    if any(_reparse(path) for path in (staging_dir, *staging_dir.parents)):
        raise ArchiveError("Thư mục staging là liên kết hoặc điểm chuyển hướng.")
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = staging_dir.resolve()
    total = 0
    rows = []
    for index, entry in enumerate(sorted(entries, key=lambda row: (_normalized_name(row["Name"]), row["Name"]))):
        name = entry["Name"]
        member_type = entry["Type"]
        suffix = PureWindowsPath(name).suffix.casefold()
        size_string = entry.get("Size", "")
        size = int(size_string) if re.fullmatch(r"[0-9]{1,20}", size_string) else None
        ambiguous = multiplicity[_normalized_name(name)] != 1
        identity = f"{archive_sha256}\0{name}" + (f"\0{index}" if ambiguous else "")
        member_id = hashlib.sha256(identity.encode()).hexdigest()
        row = {"member_id": member_id, "name": name, "size": size, "type": member_type, "status": "blocked"}
        rows.append(row)
        if not _safe_name(name):
            row["reason"] = "Tên mục hoặc đường dẫn không an toàn trên Windows."
        elif ambiguous:
            row["reason"] = "Tên mục trùng hoặc không phân biệt được khi bỏ dấu phân cách và chữ hoa."
        elif member_type == "Directory":
            row.update(status="skipped", reason="Mục là thư mục.")
        elif (member_type != "File" or "Target" in entry or "Redir type" in entry
              or entry.get("Attributes", "").startswith("l") or "encrypted" in entry.get("Flags", "").casefold()):
            row["reason"] = "Mục không phải tệp thường hoặc có liên kết/mật khẩu."
        elif suffix not in _SUPPORTED:
            row.update(status="skipped", reason="Chỉ tiếp nhận PDF, DOC hoặc DOCX trong RAR.")
        elif size is None or not 0 < size <= _MAX_MEMBER or total + size > _MAX_TOTAL:
            row["reason"] = "Mục vượt giới hạn kích thước hoặc thiếu kích thước hợp lệ."
        else:
            try:
                content = _read_command([exe, "p", "-inul", *_UNRAR_SWITCHES, str(archive_path), name], size, deadline)
                if len(content) != size or not _member_bytes_valid(suffix, content):
                    raise ArchiveError("Kích thước hoặc chữ ký của tài liệu giải nén không khớp.")
                crc = entry.get("CRC32")
                if crc and f"{zlib.crc32(content):08X}" != crc.upper():
                    raise ArchiveError("CRC32 của tài liệu giải nén không khớp danh sách.")
                total += size
                destination = staging_dir / (member_id + suffix)
                if _reparse(destination):
                    raise ArchiveError("Tệp staging là liên kết hoặc điểm chuyển hướng.")
                sha256 = hashlib.sha256(content).hexdigest()
                if not destination.is_file() or digest(destination) != sha256:
                    temporary = staging_dir / (member_id + "." + uuid.uuid4().hex + ".tmp")
                    try:
                        with temporary.open("xb") as stream:
                            stream.write(content)
                        os.replace(temporary, destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                row.update(status="staged", path=str(destination), sha256=sha256)
            except (ArchiveError, OSError) as exc:
                row["reason"] = str(exc)
    if digest(archive_path) != archive_sha256:
        raise ArchiveError("Nội dung archive đã thay đổi trong lúc đọc.")
    version = re.search(rb"(?m)^UNRAR\s+([0-9.]+)\s", listing)
    return {"revision": ARCHIVE_REVISION, "archive_path": str(archive_path),
            "archive_sha256": archive_sha256, "extractor_version": version[1].decode("ascii") if version else None,
            "members": rows}
