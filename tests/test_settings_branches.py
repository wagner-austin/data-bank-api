from __future__ import annotations

from _pytest.monkeypatch import MonkeyPatch

from data_bank_api.config import Settings


def test_defaults_when_env_missing(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_ROOT", raising=False)
    monkeypatch.delenv("MIN_FREE_GB", raising=False)
    monkeypatch.delenv("DELETE_STRICT_404", raising=False)
    monkeypatch.delenv("MAX_FILE_BYTES", raising=False)
    monkeypatch.delenv("API_UPLOAD_KEYS", raising=False)
    monkeypatch.delenv("API_READ_KEYS", raising=False)
    monkeypatch.delenv("API_DELETE_KEYS", raising=False)

    s = Settings.from_env()
    assert s.data_root == "/data/files"
    assert s.min_free_gb == 1
    assert s.delete_strict_404 is False
    assert s.max_file_bytes == 0
    assert s.api_upload_keys == frozenset()
    assert s.api_read_keys == frozenset()
    assert s.api_delete_keys == frozenset()


def test_blank_strings_fall_back_to_defaults(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_ROOT", "  ")
    monkeypatch.setenv("MIN_FREE_GB", "  ")
    monkeypatch.setenv("DELETE_STRICT_404", "  ")
    monkeypatch.setenv("MAX_FILE_BYTES", "  ")
    s = Settings.from_env()
    assert s.data_root == "/data/files"
    assert s.min_free_gb == 1
    assert s.delete_strict_404 is False
    assert s.max_file_bytes == 0


def test_delete_strict_truthy_and_falsy_variants(monkeypatch: MonkeyPatch) -> None:
    truthy = ["1", "true", "TRUE", "TrUe", "yes", "YeS"]
    falsy = ["0", "false", "FALSE", "fAlSe", "no", "  ", "nO"]

    for v in truthy:
        monkeypatch.setenv("DELETE_STRICT_404", v)
        assert Settings.from_env().delete_strict_404 is True

    for v in falsy:
        monkeypatch.setenv("DELETE_STRICT_404", v)
        assert Settings.from_env().delete_strict_404 is False


def test_api_keys_parsing_trims_and_dedups(monkeypatch: MonkeyPatch) -> None:
    # Deliberate spaces and duplicates
    monkeypatch.setenv("API_UPLOAD_KEYS", " a , a , b ,  c ")
    s = Settings.from_env()
    assert s.api_upload_keys == frozenset({"a", "b", "c"})


def test_read_delete_override_and_fallback(monkeypatch: MonkeyPatch) -> None:
    # When read/delete unset, they fall back to upload
    monkeypatch.setenv("API_UPLOAD_KEYS", "u1,u2")
    monkeypatch.delenv("API_READ_KEYS", raising=False)
    monkeypatch.delenv("API_DELETE_KEYS", raising=False)
    s = Settings.from_env()
    assert s.api_read_keys == frozenset({"u1", "u2"})
    assert s.api_delete_keys == frozenset({"u1", "u2"})

    # When explicitly set, they override upload
    monkeypatch.setenv("API_READ_KEYS", "r1, r2")
    monkeypatch.setenv("API_DELETE_KEYS", "d1")
    s2 = Settings.from_env()
    assert s2.api_upload_keys == frozenset({"u1", "u2"})
    assert s2.api_read_keys == frozenset({"r1", "r2"})
    assert s2.api_delete_keys == frozenset({"d1"})


def test_explicit_empty_read_delete_still_fallback(monkeypatch: MonkeyPatch) -> None:
    # Empty strings are treated as unset and fall back to upload keys
    monkeypatch.setenv("API_UPLOAD_KEYS", "u1")
    monkeypatch.setenv("API_READ_KEYS", "")
    monkeypatch.setenv("API_DELETE_KEYS", "  ")
    s = Settings.from_env()
    assert s.api_read_keys == frozenset({"u1"})
    assert s.api_delete_keys == frozenset({"u1"})


def test_min_free_and_max_bytes_boundaries(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MIN_FREE_GB", "0")
    monkeypatch.setenv("MAX_FILE_BYTES", str(2**31))
    s = Settings.from_env()
    assert s.min_free_gb == 0
    assert s.max_file_bytes == 2**31


def test_invalid_integers_raise(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("MIN_FREE_GB", "abc")
    try:
        Settings.from_env()
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for invalid MIN_FREE_GB")

    # Ensure MAX_FILE_BYTES invalid also raises
    monkeypatch.setenv("MIN_FREE_GB", "1")
    monkeypatch.setenv("MAX_FILE_BYTES", "not-a-number")
    try:
        Settings.from_env()
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError for invalid MAX_FILE_BYTES")
