"""Application configuration.

Deliberately dependency-light: reads a .env file (if python-dotenv is
installed) and then plain environment variables, so the package imports
cleanly in constrained CI environments.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

try:  # pragma: no cover - trivial import guard
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except Exception:  # pragma: no cover
    pass


def _get(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _get_bool(name: str, default: bool = False) -> bool:
    return _get(name, "true" if default else "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name, str(default)))
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name, str(default)))
    except ValueError:
        return default


def _resolve(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    app_name: str = "DemoBank Loan Eligibility Assistant"
    app_env: str = field(default_factory=lambda: _get("APP_ENV", "local"))
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))
    api_host: str = field(default_factory=lambda: _get("API_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: _get_int("API_PORT", 8000))

    # LLM
    anthropic_api_key: str = field(default_factory=lambda: _get("ANTHROPIC_API_KEY"))
    claude_model: str = field(default_factory=lambda: _get("CLAUDE_MODEL", "claude-sonnet-4-5"))
    claude_max_tokens: int = field(default_factory=lambda: _get_int("CLAUDE_MAX_TOKENS", 900))
    claude_temperature: float = field(
        default_factory=lambda: _get_float("CLAUDE_TEMPERATURE", 0.2)
    )
    enable_llm: bool = field(default_factory=lambda: _get_bool("ENABLE_LLM", True))

    # RAG
    embedding_model: str = field(
        default_factory=lambda: _get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    )
    embedding_backend: str = field(default_factory=lambda: _get("EMBEDDING_BACKEND", "auto"))
    vector_backend: str = field(default_factory=lambda: _get("VECTOR_BACKEND", "auto"))
    chroma_dir: Path = field(default_factory=lambda: _resolve(_get("CHROMA_DIR", "./var/chroma")))
    chroma_collection: str = field(
        default_factory=lambda: _get("CHROMA_COLLECTION", "demobank_policies")
    )
    retrieval_top_k: int = field(default_factory=lambda: _get_int("RETRIEVAL_TOP_K", 5))

    # Policy / rules
    default_policy_version: str = field(
        default_factory=lambda: _get("DEFAULT_POLICY_VERSION", "2.0")
    )
    policies_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "policies")
    rules_dir: Path = field(default_factory=lambda: BASE_DIR / "app" / "rules" / "versions")
    applicants_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "applicants")

    # Audit
    audit_db_url: str = field(
        default_factory=lambda: _get("AUDIT_DB_URL", "sqlite:///./var/audit.db")
    )

    @property
    def llm_enabled(self) -> bool:
        """True only when Claude can actually be called."""
        return bool(self.enable_llm and self.anthropic_api_key)

    def ensure_dirs(self) -> None:
        (BASE_DIR / "var").mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests after mutating the environment."""
    get_settings.cache_clear()
