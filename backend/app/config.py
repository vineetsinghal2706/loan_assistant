from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent  # .../backend


class Settings(BaseSettings):
    """Central configuration. Values are read from environment variables first
    (this is how docker-compose / CI inject them), falling back to backend/.env
    for local, non-Docker development, then to the defaults below.
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # "stub" runs fully offline with a deterministic templated answer - used
    # by default in tests/CI so the regression gate does not need API keys.
    # "anthropic" streams real answers from the Anthropic API.
    llm_provider: str = "stub"
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-5"

    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Which versioned rule set is currently "in force" for eligibility
    # decisions and for citing "the rule version used" in the audit log.
    current_rule_version: str = "v2"
    top_k: int = 5

    policy_dir: Path = BASE_DIR / "data" / "policies"
    rules_dir: Path = BASE_DIR / "data" / "eligibility_rules"
    index_dir: Path = BASE_DIR / "data" / "index"
    eval_set_path: Path = BASE_DIR / "data" / "eval" / "labelled_eligibility_set.jsonl"
    audit_log_path: Path = BASE_DIR / "data" / "audit" / "audit_log.jsonl"


settings = Settings()
settings.index_dir.mkdir(parents=True, exist_ok=True)
settings.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
