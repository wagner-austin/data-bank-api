from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    data_root: str = Field(default="/data/files", alias="DATA_ROOT")
    min_free_gb: int = Field(default=1, alias="MIN_FREE_GB")
    delete_strict_404: bool = Field(default=False, alias="DELETE_STRICT_404")

    class Config:
        case_sensitive = False
        env_file = ".env"

