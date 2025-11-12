from __future__ import annotations

import logging

from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from data_bank_api.config import Settings
from data_bank_api.logging import setup_logging


def test_settings_from_env_reads_values(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_ROOT", "/x")
    monkeypatch.setenv("MIN_FREE_GB", "7")
    monkeypatch.setenv("DELETE_STRICT_404", "true")
    monkeypatch.setenv("MAX_FILE_BYTES", "1234")
    s = Settings.from_env()
    assert s.data_root == "/x"
    assert s.min_free_gb == 7
    assert s.delete_strict_404 is True
    assert s.max_file_bytes == 1234


def test_json_logging_includes_exc_info(capfd: CaptureFixture[str]) -> None:
    setup_logging("INFO")
    log = logging.getLogger("test")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed")
    out = capfd.readouterr().out.strip().splitlines()[-1]

    assert "ERROR" in out
    assert "test" in out
    assert "exc_info" in out


def test_settings_api_keys_from_env(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("API_UPLOAD_KEYS", "u1,u2")
    # leave read/delete unset to inherit from upload
    s = Settings.from_env()
    assert s.api_upload_keys == frozenset({"u1", "u2"})
    assert s.api_read_keys == s.api_upload_keys
    assert s.api_delete_keys == s.api_upload_keys
