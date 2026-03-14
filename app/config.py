from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_APP_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _APP_DIR.parent


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _path_env(name: str, default: Path) -> Path:
    value = os.getenv(name)
    path = Path(value).expanduser() if value else default
    if path.is_absolute():
        return path
    return (_PROJECT_ROOT / path).resolve()


_DEFAULT_DATA_DIR = _path_env("DATA_DIR", _PROJECT_ROOT / "data")
_DEFAULT_TENDER_UPLOAD_DIR = _path_env(
    "TENDER_UPLOAD_DIR",
    _DEFAULT_DATA_DIR / "uploads" / "tenders",
)
_DEFAULT_BID_OUTPUT_DIR = _path_env(
    "BID_OUTPUT_DIR",
    _DEFAULT_DATA_DIR / "outputs" / "bids",
)
_DEFAULT_VECTOR_DB_PATH = _path_env(
    "VECTOR_DB_PATH",
    _DEFAULT_DATA_DIR / "chroma",
)


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Bid-Agent-Service")
    app_version: str = os.getenv("APP_VERSION", "0.1.0")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = _int_env("APP_PORT", 8000)
    app_reload: bool = _bool_env("APP_RELOAD", True)
    project_root: str = str(_PROJECT_ROOT)
    data_dir: str = str(_DEFAULT_DATA_DIR)
    tender_upload_dir: str = str(_DEFAULT_TENDER_UPLOAD_DIR)
    bid_output_dir: str = str(_DEFAULT_BID_OUTPUT_DIR)

    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_temperature: float = _float_env("LLM_TEMPERATURE", 0.2)
    llm_max_tokens: int = _int_env("LLM_MAX_TOKENS", 2000)
    # 0 表示不限制招标文件输入长度
    tender_parse_char_limit: int = _int_env("TENDER_PARSE_CHAR_LIMIT", 0)

    embedding_model_name: str = os.getenv(
        "EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2"
    )

    vector_db_path: str = str(_DEFAULT_VECTOR_DB_PATH)
    vector_collection_name: str = os.getenv("VECTOR_COLLECTION_NAME", "bid_knowledge")

    default_chunk_size: int = _int_env("DEFAULT_CHUNK_SIZE", 900)
    default_chunk_overlap: int = _int_env("DEFAULT_CHUNK_OVERLAP", 150)
    default_top_k: int = _int_env("DEFAULT_TOP_K", 5)

    team_max_turns: int = _int_env("TEAM_MAX_TURNS", 8)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
