"""Deterministic, versioned eligibility rule engine.

Nothing in this package imports FastAPI, Anthropic or ChromaDB: the engine is
the source of truth for eligibility and must be testable in isolation.
"""

from app.rules.engine import RuleEngine
from app.rules.registry import (
    PolicyPack,
    PolicyRegistry,
    PolicyVersionNotFound,
    ProductConfig,
    ProductNotSupported,
    Rule,
    get_registry,
)

__all__ = [
    "RuleEngine",
    "PolicyPack",
    "PolicyRegistry",
    "PolicyVersionNotFound",
    "ProductConfig",
    "ProductNotSupported",
    "Rule",
    "get_registry",
]
