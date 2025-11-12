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
    meta = s.save_stream(io.BytesIO(b"0123456789"), "text/plain")
    # invalid: end < start
    with pytest.raises(StorageError):
        s.open_range(meta.file_id, 5, 2)
    # unsatisfiable: start > last
    with pytest.raises(StorageError):
        s.open_range(meta.file_id, 100, None)


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
        s.save_stream(io.BytesIO(b"data"), "text/plain")
    # ensure no upload_* tmp files remain
    parts = (tmp_path / "files" / "aa" / "bb").glob("upload_*")
    assert list(parts) == []


def test_save_stream_cleanup_unlink_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _storage(tmp_path)
    monkeypatch.setattr(os, "replace", _raise_oserror_fail)
    # make unlink also fail to exercise except branch in cleanup
    monkeypatch.setattr(os, "unlink", _raise_oserror_unlink)
    with pytest.raises(OSError):
        s.save_stream(io.BytesIO(b"data"), "text/plain")


def test_insufficient_space_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # configure high min_free to trigger guard
    s = Storage(root=tmp_path / "files", min_free_gb=1_000_000)
    with pytest.raises(InsufficientStorageError):
        s.save_stream(io.BytesIO(b"x"), "text/plain")


def test_meta_path_invalid_file_id_raises(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    with pytest.raises(StorageError):
        _ = s._meta_path_for("zz")


def test_head_fallback_without_sidecar(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    meta = s.save_stream(io.BytesIO(b"abcdef"), "text/plain")
    # Remove sidecar to force fallback code path in head()
    mpath = s._meta_path_for(meta.file_id)
    assert mpath.exists()
    mpath.unlink()
    info = s.head(meta.file_id)
    assert info.sha256 == meta.sha256
    # Without sidecar, content_type falls back to octet-stream
    assert info.content_type == "application/octet-stream"


def test_read_sidecar_oserror_branch(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    meta = s.save_stream(io.BytesIO(b"xyz"), "text/plain")
    mpath = s._meta_path_for(meta.file_id)
    # Replace sidecar file with a directory to trigger OSError on open
    mpath.unlink()
    mpath.mkdir()
    info = s.head(meta.file_id)
    assert info.sha256 == meta.sha256


def test_meta_path_valid_return(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    meta = s.save_stream(io.BytesIO(b"q"), "text/plain")
    meta_path = s._meta_path_for(meta.file_id)
    # Ensure helper returns the actual path
    assert meta_path.exists()


def test_sidecar_replace_and_cleanup_unlink_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _storage(tmp_path)
    # Patch os.replace to fail only for meta temp replace
    real_replace = os.replace
    real_unlink = os.unlink

    def _replace(src: str, dst: str) -> None:
        from pathlib import Path as PathMod

        if PathMod(src).name.startswith("meta_"):
            raise OSError("meta replace fail")
        real_replace(src, dst)

    def _unlink(path: str) -> None:
        from pathlib import Path as PathMod

        if PathMod(path).name.startswith("meta_"):
            raise OSError("meta unlink fail")
        real_unlink(path)

    monkeypatch.setattr(os, "replace", _replace)
    monkeypatch.setattr(os, "unlink", _unlink)
    meta = s.save_stream(io.BytesIO(b"sidecar"), "text/plain")
    # save_stream still succeeds even if sidecar fails; ensure sha is present
    assert len(meta.sha256) == 64


def test_sidecar_present_but_invalid_values(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    meta = s.save_stream(io.BytesIO(b"abcdef"), "text/plain")
    mpath = s._meta_path_for(meta.file_id)
    # Write invalid sidecar values to exercise negative branches
    mpath.write_text("sha256=z\ncontent_type=\ncreated_at=\n", encoding="utf-8")
    info = s.head(meta.file_id)
    assert info.sha256 == meta.sha256
    assert info.content_type == "application/octet-stream"


def test_sidecar_present_empty_file(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    meta = s.save_stream(io.BytesIO(b"abcdef"), "text/plain")
    mpath = s._meta_path_for(meta.file_id)
    # Overwrite sidecar with empty content to exercise zero-iteration branch
    mpath.write_text("", encoding="utf-8")
    info = s.head(meta.file_id)
    assert info.sha256 == meta.sha256
    assert info.content_type == "application/octet-stream"


def test_sidecar_present_unrelated_line(tmp_path: Path) -> None:
    s = _storage(tmp_path)
    meta = s.save_stream(io.BytesIO(b"abcdef"), "text/plain")
    mpath = s._meta_path_for(meta.file_id)
    # Sidecar contains an unrelated line to exercise no-op branch in loop
    mpath.write_text("ignored=1\n", encoding="utf-8")
    info = s.head(meta.file_id)
    assert info.sha256 == meta.sha256
    assert info.content_type == "application/octet-stream"
