from pathlib import Path

import yaml

from ..models import ApplicantProfile, EligibilityDecision

# Rule conditions are short boolean expressions authored by developers in the
# YAML rule files under backend/data/eligibility_rules/ (not user input).
# They are evaluated with no builtins available, against only the applicant
# fields, which is adequate isolation for this trusted, developer-authored
# configuration. Do not extend this to evaluate untrusted input.
_EVAL_GLOBALS = {"__builtins__": {}}

# Below this credit score, an R-CS-01 failure is treated as an automatic
# decline. Between this value and the rule's own threshold, it is routed to
# manual review ("needs_review") instead of an automatic decline.
_SOFT_REVIEW_CREDIT_FLOOR = 630


class RulesEngine:
    def __init__(self, rules_dir: Path):
        self.rules_dir = rules_dir
        self.rule_sets: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self.rules_dir.glob("rules_*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.rule_sets[data["version"]] = data

    def available_versions(self) -> list[str]:
        return sorted(self.rule_sets.keys())

    def get_ruleset(self, version: str) -> dict:
        if version not in self.rule_sets:
            raise KeyError(f"Unknown rule version '{version}'. Available: {self.available_versions()}")
        return self.rule_sets[version]

    def latest_version(self) -> str:
        return sorted(self.rule_sets, key=lambda v: self.rule_sets[v]["effective_date"])[-1]

    def evaluate(self, applicant: ApplicantProfile, version: str) -> EligibilityDecision:
        ruleset = self.get_ruleset(version)
        context = applicant.model_dump()

        failed: list[dict] = []
        soft_failed: list[dict] = []
        rule_ids: list[str] = []

        for rule in ruleset["rules"]:
            rule_ids.append(rule["id"])
            try:
                passed = eval(rule["condition"], _EVAL_GLOBALS, context)  # noqa: S307
            except Exception as exc:  # pragma: no cover - defensive only
                passed = False
                rule = {**rule, "description": f"{rule['description']} (evaluation error: {exc})"}

            if passed:
                continue

            if rule["id"] == "R-CS-01" and context["credit_score"] >= _SOFT_REVIEW_CREDIT_FLOOR:
                soft_failed.append(rule)
            else:
                failed.append(rule)

        if not failed and not soft_failed:
            outcome = "eligible"
        elif not failed and soft_failed:
            outcome = "needs_review"
        else:
            outcome = "not_eligible"

        if outcome == "eligible":
            reasons = [f"All rules satisfied under policy {version} ({ruleset['effective_date']})."]
        else:
            reasons = [f"{r['id']}: {r['description']} ({r['section']})" for r in failed + soft_failed]

        return EligibilityDecision(
            outcome=outcome,
            reasons=reasons,
            rule_ids=rule_ids,
            rule_version=version,
        )
