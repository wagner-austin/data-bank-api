from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class FileMetadata:
    file_id: str
    size_bytes: int
    sha256: str
    content_type: str
    created_at: str | None


class StorageError(Exception):
    pass


class InsufficientStorageError(StorageError):
    pass


class StoredFileNotFoundError(StorageError):
    pass


class FileTooLargeError(StorageError):
    pass


def _is_hex(s: str) -> bool:
    return all(c in "0123456789abcdef" for c in s)


class Storage:
    def __init__(self: Storage, root: Path, min_free_gb: int, *, max_file_bytes: int = 0) -> None:
        self._root = root
        self._min_free_bytes = int(min_free_gb) * 1024 * 1024 * 1024
        self._max_file_bytes = int(max_file_bytes) if max_file_bytes is not None else 0
        self._logger = logging.getLogger(__name__)

    def _path_for(self: Storage, file_id: str) -> Path:
        fid = file_id.strip().lower()
        if len(fid) < 4 or not _is_hex(fid):
            raise StorageError("invalid file_id")
        sub1, sub2 = fid[:2], fid[2:4]
        return self._root / sub1 / sub2 / f"{fid}.bin"

    def _meta_path_for(self: Storage, file_id: str) -> Path:
        fid = file_id.strip().lower()
        if len(fid) < 4 or not _is_hex(fid):
            raise StorageError("invalid file_id")
        sub1, sub2 = fid[:2], fid[2:4]
        return self._root / sub1 / sub2 / f"{fid}.meta"

    def _read_sidecar(self: Storage, file_id: str) -> tuple[str | None, str | None, str | None]:
        """Read sidecar metadata if present.

        Returns (sha256, content_type, created_at). Each field can be None if
        missing or invalid.
        """
        sha: str | None = None
        ctype: str | None = None
        created_at: str | None = None
        mpath = self._meta_path_for(file_id)
        try:
            if mpath.exists():
                with mpath.open("r", encoding="utf-8", errors="ignore") as mf:
                    for line in mf:
                        if line.startswith("sha256="):
                            v = line[len("sha256=") :].strip()
                            if _is_hex(v):
                                sha = v
                        elif line.startswith("content_type="):
                            v2 = line[len("content_type=") :].strip()
                            if v2 != "":
                                ctype = v2
                        elif line.startswith("created_at="):
                            v3 = line[len("created_at=") :].strip()
                            if v3 != "":
                                created_at = v3
        except OSError:
            pass
        return sha, ctype, created_at

    def _ensure_free_space(self: Storage) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self._root)
        free_bytes = int(usage.free)
        if free_bytes < self._min_free_bytes:
            raise InsufficientStorageError("insufficient free space")

    def save_stream(self: Storage, stream: BinaryIO, content_type: str) -> FileMetadata:
        """Save stream to storage using server-generated sha256 file_id.

        Writes to a temp file in the storage root, computes sha256 and total size,
        enforces max size if configured, then atomically renames to the final
        hierarchical path. Also writes a small sidecar metadata file containing
        content_type and created_at for faster HEAD/INFO.
        """
        self._ensure_free_space()
        self._root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="upload_", dir=str(self._root))
        size = 0
        h = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)
                    if self._max_file_bytes > 0 and size > self._max_file_bytes:
                        raise FileTooLargeError("file too large")
                    h.update(chunk)
                f.flush()
                os.fsync(f.fileno())
            file_id = h.hexdigest()
            target = self._path_for(file_id)
            target_parent = target.parent
            target_parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, target)
            created_at = datetime.now(tz=UTC).isoformat()
            # Write sidecar metadata atomically under the final directory
            meta_tmp_fd, meta_tmp = tempfile.mkstemp(prefix="meta_", dir=str(target_parent))
            try:
                with os.fdopen(meta_tmp_fd, "w", encoding="utf-8") as mf:
                    mf.write(f"sha256={file_id}\n")
                    mf.write(f"content_type={content_type}\n")
                    mf.write(f"created_at={created_at}\n")
                    mf.flush()
                    os.fsync(mf.fileno())
                os.replace(meta_tmp, self._meta_path_for(file_id))
            finally:
                try:
                    if os.path.exists(meta_tmp):
                        os.unlink(meta_tmp)
                except OSError:
                    pass
            return FileMetadata(
                file_id=file_id,
                size_bytes=size,
                sha256=file_id,
                content_type=content_type,
                created_at=created_at,
            )
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass

    def head(self: Storage, file_id: str) -> FileMetadata:
        path = self._path_for(file_id)
        if not path.exists() or not path.is_file():
            raise StoredFileNotFoundError(file_id)
        size = path.stat().st_size
        # Try sidecar metadata for sha256/content_type/created_at; fall back safely
        sha, ctype, created_at = self._read_sidecar(file_id)
        if sha is None:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
            sha = h.hexdigest()
        if ctype is None:
            ctype = "application/octet-stream"
        return FileMetadata(
            file_id=file_id.strip().lower(),
            size_bytes=size,
            sha256=sha,
            content_type=ctype,
            created_at=created_at,
        )

    def open_range(
        self: Storage, file_id: str, start: int, end_inclusive: int | None
    ) -> tuple[Iterator[bytes], int, int]:
        path = self._path_for(file_id)
        if not path.exists() or not path.is_file():
            raise StoredFileNotFoundError(file_id)
        size = path.stat().st_size
        if start < 0 or (end_inclusive is not None and end_inclusive < start):
            raise StorageError("invalid range")
        last = size - 1 if end_inclusive is None or end_inclusive > size - 1 else end_inclusive
        if start > last:
            raise StorageError("unsatisfiable range")

        def _iter() -> Iterator[bytes]:
            with path.open("rb") as f:
                f.seek(start)
                to_read = last - start + 1
                while to_read > 0:
                    chunk = f.read(min(1024 * 1024, to_read))
                    if not chunk:  # pragma: no cover - defensive
                        break
                    yield chunk
                    to_read -= len(chunk)

        return _iter(), start, last

    def delete(self: Storage, file_id: str) -> bool:
        path = self._path_for(file_id)
        meta_path = self._meta_path_for(file_id)
        existed = False
        try:
            path.unlink()
            existed = True
        except FileNotFoundError:
            # Blob already missing; proceed to sidecar cleanup below.
            pass

        if meta_path.exists():
            meta_path.unlink()
            existed = True

        return existed

    def get_size(self: Storage, file_id: str) -> int:
        path = self._path_for(file_id)
        if not path.exists() or not path.is_file():
            raise StoredFileNotFoundError(file_id)
        return int(path.stat().st_size)
