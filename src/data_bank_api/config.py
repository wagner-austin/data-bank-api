from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Settings:
    data_root: str = "/data/files"
    min_free_gb: int = 1
    delete_strict_404: bool = False
    api_upload_keys: frozenset[str] = frozenset()
    api_read_keys: frozenset[str] = frozenset()
    api_delete_keys: frozenset[str] = frozenset()

    @staticmethod
    def _get_env_str(name: str, default: str) -> str:
        v = os.getenv(name)
        return v if v is not None and v.strip() != "" else default

    @staticmethod
    def _csv_env_set(name: str) -> frozenset[str]:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return frozenset()
        parts = [p.strip() for p in raw.split(",") if p.strip() != ""]
        return frozenset(parts)

    @classmethod
    def from_env(cls: type[Settings]) -> Settings:
        root = cls._get_env_str("DATA_ROOT", "/data/files")
        min_free = cls._get_env_str("MIN_FREE_GB", "1")
        strict = cls._get_env_str("DELETE_STRICT_404", "false").lower() in {"1", "true", "yes"}

        upload_keys: Final[frozenset[str]] = cls._csv_env_set("API_UPLOAD_KEYS")
        read_keys: Final[frozenset[str]] = cls._csv_env_set("API_READ_KEYS") or upload_keys
        delete_keys: Final[frozenset[str]] = cls._csv_env_set("API_DELETE_KEYS") or upload_keys

        return cls(
            data_root=root,
            min_free_gb=int(min_free),
            delete_strict_404=strict,
            api_upload_keys=upload_keys,
            api_read_keys=read_keys,
            api_delete_keys=delete_keys,
        )
