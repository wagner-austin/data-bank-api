from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class FileMetadata:
    file_id: str
    size_bytes: int
    sha256: str
    content_type: str


class StorageError(Exception):
    pass


class InsufficientStorageError(StorageError):
    pass


class StoredFileNotFoundError(StorageError):
    pass


def _is_hex(s: str) -> bool:
    return all(c in "0123456789abcdef" for c in s)


class Storage:
    def __init__(self: Storage, root: Path, min_free_gb: int) -> None:
        self._root = root
        self._min_free_bytes = int(min_free_gb) * 1024 * 1024 * 1024

    def _path_for(self: Storage, file_id: str) -> Path:
        fid = file_id.strip().lower()
        if len(fid) < 4 or not _is_hex(fid):
            raise StorageError("invalid file_id")
        sub1, sub2 = fid[:2], fid[2:4]
        return self._root / sub1 / sub2 / f"{fid}.bin"

    def _ensure_free_space(self: Storage) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self._root)
        free_bytes = int(usage.free)
        if free_bytes < self._min_free_bytes:
            raise InsufficientStorageError("insufficient free space")

    def save_stream(
        self: Storage, file_id: str, stream: BinaryIO, content_type: str
    ) -> FileMetadata:
        self._ensure_free_space()
        target = self._path_for(file_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="upload_", dir=str(target.parent))
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
                    h.update(chunk)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
        return FileMetadata(
            file_id=file_id.strip().lower(),
            size_bytes=size,
            sha256=h.hexdigest(),
            content_type=content_type,
        )

    def head(self: Storage, file_id: str) -> FileMetadata:
        path = self._path_for(file_id)
        if not path.exists() or not path.is_file():
            raise StoredFileNotFoundError(file_id)
        size = path.stat().st_size
        # Compute sha256 lazily only when needed; for now, read file once
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return FileMetadata(
            file_id=file_id.strip().lower(),
            size_bytes=size,
            sha256=h.hexdigest(),
            content_type="application/octet-stream",
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
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def get_size(self: Storage, file_id: str) -> int:
        path = self._path_for(file_id)
        if not path.exists() or not path.is_file():
            raise StoredFileNotFoundError(file_id)
        return int(path.stat().st_size)
