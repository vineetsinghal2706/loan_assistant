"""Versioned rule pack registry.

Rule packs are declarative YAML files under ``app/rules/versions``. Keeping the
rules out of Python means a policy change is a reviewable data change, and it
lets the API expose "which version was applied" as a first-class answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

import yaml

from app.config import get_settings
from app.schemas import PolicyVersionInfo, RuleSeverity


class PolicyVersionNotFound(KeyError):
    """Requested policy version is not registered."""


class ProductNotSupported(KeyError):
    """Requested product is not covered by the resolved policy version."""


def version_sort_key(version: str) -> tuple:
    parts: List[int] = []
    for chunk in str(version).split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _as_date(value: Any) -> Optional[date]:
    if value in (None, "", "null"):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    category: str
    severity: RuleSeverity
    condition: Mapping[str, Any]
    policy_reference: Mapping[str, Any]
    applies_when: Optional[Mapping[str, Any]] = None
    threshold: Optional[Any] = None
    observe: List[str] = field(default_factory=list)
    message_pass: str = ""
    message_fail: str = ""
    message_unknown: str = ""
    remediation: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], policy_version: str) -> "Rule":
        missing = [key for key in ("id", "title", "severity", "condition") if key not in raw]
        if missing:
            raise ValueError(f"Rule is missing required keys {missing}: {raw!r}")
        reference = dict(raw.get("policy_reference") or {})
        if "section" not in reference or "document_id" not in reference:
            raise ValueError(
                f"Rule {raw['id']} must cite a policy clause (document_id + section)."
            )
        reference.setdefault("policy_version", policy_version)
        return cls(
            id=str(raw["id"]),
            title=str(raw["title"]),
            category=str(raw.get("category", "general")),
            severity=RuleSeverity(str(raw["severity"]).upper()),
            condition=raw["condition"],
            policy_reference=reference,
            applies_when=raw.get("applies_when"),
            threshold=raw.get("threshold"),
            observe=list(raw.get("observe") or []),
            message_pass=str(raw.get("message_pass", "")),
            message_fail=str(raw.get("message_fail", "")),
            message_unknown=str(raw.get("message_unknown", "")),
            remediation=str(raw.get("remediation", "")),
        )


@dataclass(frozen=True)
class ProductConfig:
    product: str
    display_name: str
    assumptions: Mapping[str, Any]
    limits: Mapping[str, Any]
    rules: List[Rule]

    @classmethod
    def from_dict(
        cls, product: str, raw: Mapping[str, Any], policy_version: str
    ) -> "ProductConfig":
        return cls(
            product=product,
            display_name=str(raw.get("display_name", product)),
            assumptions=dict(raw.get("assumptions") or {}),
            limits=dict(raw.get("limits") or {}),
            rules=[Rule.from_dict(item, policy_version) for item in raw.get("rules") or []],
        )


@dataclass(frozen=True)
class PolicyPack:
    policy_version: str
    title: str
    status: str
    effective_from: date
    effective_to: Optional[date]
    summary: str
    change_log: List[str]
    documents: List[str]
    products: Dict[str, ProductConfig]
    source_path: Optional[Path] = None

    def product(self, product: str) -> ProductConfig:
        key = getattr(product, "value", product)
        if key not in self.products:
            raise ProductNotSupported(
                f"Product {key} is not covered by policy version {self.policy_version}"
            )
        return self.products[key]

    def is_in_force(self, on: date) -> bool:
        if on < self.effective_from:
            return False
        return self.effective_to is None or on <= self.effective_to

    @property
    def rule_count(self) -> int:
        return sum(len(cfg.rules) for cfg in self.products.values())

    def to_info(self) -> PolicyVersionInfo:
        return PolicyVersionInfo(
            policy_version=self.policy_version,
            title=self.title,
            status=self.status,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            summary=self.summary,
            change_log=list(self.change_log),
            products=sorted(self.products.keys()),
            rule_count=self.rule_count,
            documents=list(self.documents),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], source_path: Optional[Path] = None) -> "PolicyPack":
        version = str(raw["policy_version"])
        products = {
            name: ProductConfig.from_dict(name, cfg, version)
            for name, cfg in (raw.get("products") or {}).items()
        }
        effective_from = _as_date(raw.get("effective_from"))
        if effective_from is None:
            raise ValueError(f"Policy pack {version} must declare effective_from")
        return cls(
            policy_version=version,
            title=str(raw.get("title", f"DemoBank policy {version}")),
            status=str(raw.get("status", "active")),
            effective_from=effective_from,
            effective_to=_as_date(raw.get("effective_to")),
            summary=str(raw.get("summary", "")),
            change_log=list(raw.get("change_log") or []),
            documents=list(raw.get("documents") or []),
            products=products,
            source_path=source_path,
        )


class PolicyRegistry:
    """Loads and resolves versioned rule packs."""

    def __init__(self, rules_dir: Optional[Path] = None) -> None:
        settings = get_settings()
        self.rules_dir = Path(rules_dir) if rules_dir else settings.rules_dir
        self._packs: Dict[str, PolicyPack] = {}
        self.load()

    # -- loading ---------------------------------------------------------
    def load(self) -> None:
        self._packs = {}
        if not self.rules_dir.exists():
            raise FileNotFoundError(f"Rule pack directory not found: {self.rules_dir}")
        for path in sorted(self.rules_dir.glob("rules_v*.y*ml")):
            with path.open("r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle)
            pack = PolicyPack.from_dict(raw, source_path=path)
            if pack.policy_version in self._packs:
                raise ValueError(f"Duplicate policy version {pack.policy_version} in {path}")
            self._packs[pack.policy_version] = pack
        if not self._packs:
            raise FileNotFoundError(f"No rule packs found in {self.rules_dir}")
        self._validate_rule_ids()

    def _validate_rule_ids(self) -> None:
        for pack in self._packs.values():
            for cfg in pack.products.values():
                seen = set()
                for rule in cfg.rules:
                    if rule.id in seen:
                        raise ValueError(
                            f"Duplicate rule id {rule.id} in {pack.policy_version}/{cfg.product}"
                        )
                    seen.add(rule.id)

    # -- access ----------------------------------------------------------
    @property
    def versions(self) -> List[str]:
        return sorted(self._packs.keys(), key=version_sort_key)

    def get(self, policy_version: str) -> PolicyPack:
        key = str(policy_version)
        if key not in self._packs:
            raise PolicyVersionNotFound(
                f"Unknown policy version '{key}'. Available: {', '.join(self.versions)}"
            )
        return self._packs[key]

    def latest(self) -> PolicyPack:
        active = [pack for pack in self._packs.values() if pack.status == "active"]
        pool = active or list(self._packs.values())
        return max(pool, key=lambda pack: version_sort_key(pack.policy_version))

    def in_force_on(self, on: date) -> Optional[PolicyPack]:
        candidates = [pack for pack in self._packs.values() if pack.is_in_force(on)]
        if not candidates:
            return None
        return max(candidates, key=lambda pack: version_sort_key(pack.policy_version))

    def resolve(
        self,
        policy_version: Optional[str] = None,
        as_of: Optional[date] = None,
    ) -> PolicyPack:
        """Explicit version wins, then as-of date, then configured default."""
        if policy_version:
            return self.get(policy_version)
        if as_of is not None:
            pack = self.in_force_on(as_of)
            if pack is None:
                raise PolicyVersionNotFound(
                    f"No DemoBank policy version was in force on {as_of.isoformat()}"
                )
            return pack
        default = get_settings().default_policy_version
        if default and default in self._packs:
            return self._packs[default]
        return self.latest()

    def info(self, policy_version: str) -> PolicyVersionInfo:
        return self.get(policy_version).to_info()

    def all_info(self) -> List[PolicyVersionInfo]:
        return [self.get(version).to_info() for version in self.versions]

    def products(self) -> List[str]:
        names: List[str] = []
        for pack in self._packs.values():
            for name in pack.products:
                if name not in names:
                    names.append(name)
        return sorted(names)


@lru_cache(maxsize=1)
def get_registry() -> PolicyRegistry:
    return PolicyRegistry()


def reset_registry_cache() -> None:
    get_registry.cache_clear()
