from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from data_bank_api.storage import (
    InsufficientStorageError,
    Storage,
    StorageError,
    StoredFileNotFoundError,
)


def _storage(tmp_path: Path) -> Storage:
    root = tmp_path / "files"
    return Storage(root=root, min_free_gb=0)


def test_path_for_invalid_file_id_raises(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    with pytest.raises(StorageError):
        # too short and non-hex
        s.head("zz")


def test_head_and_open_range_not_found(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    with pytest.raises(StoredFileNotFoundError):
        s.head("abcd1234")
    with pytest.raises(StoredFileNotFoundError):
        s.open_range("abcd1234", 0, None)


def test_open_range_invalid_and_unsatisfiable(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    # write a small file
    s.save_stream("abcd1234", io.BytesIO(b"0123456789"), "text/plain")
    # invalid: end < start
    with pytest.raises(StorageError):
        s.open_range("abcd1234", 5, 2)
    # unsatisfiable: start > last
    with pytest.raises(StorageError):
        s.open_range("abcd1234", 100, None)


def test_get_size_not_found(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    with pytest.raises(StoredFileNotFoundError):
        s.get_size("ffffeeee")


def _raise_oserror_fail(*_args: object, **_kwargs: object) -> None:
    raise OSError("fail")


def _raise_oserror_unlink(*_args: object, **_kwargs: object) -> None:
    raise OSError("unlink fail")


def test_save_stream_cleanup_unlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _storage(tmp_path)
    # force os.replace to raise so tmp file remains for cleanup
    monkeypatch.setattr(os, "replace", _raise_oserror_fail)
    with pytest.raises(OSError):
        s.save_stream("aabbccdd", io.BytesIO(b"data"), "text/plain")
    # ensure no upload_* tmp files remain
    parts = (tmp_path / "files" / "aa" / "bb").glob("upload_*")
    assert list(parts) == []


def test_save_stream_cleanup_unlink_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _storage(tmp_path)
    monkeypatch.setattr(os, "replace", _raise_oserror_fail)
    # make unlink also fail to exercise except branch in cleanup
    monkeypatch.setattr(os, "unlink", _raise_oserror_unlink)
    with pytest.raises(OSError):
        s.save_stream("ddeeff00", io.BytesIO(b"data"), "text/plain")


def test_insufficient_space_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # configure high min_free to trigger guard
    s = Storage(root=tmp_path / "files", min_free_gb=1_000_000)
    with pytest.raises(InsufficientStorageError):
        s.save_stream("00112233", io.BytesIO(b"x"), "text/plain")
