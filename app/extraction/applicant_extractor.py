"""Deterministic applicant information extraction.

Extraction is pattern based, not model based, on purpose: in a credit context a
field that drives a hard criterion must be traceable to the exact line of the
document it came from. Every extracted field carries provenance (document,
page, matched line) and a confidence score, and anything not found is reported
as missing rather than guessed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.extraction.parsers import ParsedDocument, parse_bytes, parse_path
from app.schemas import (
    ApplicantProfile,
    EmploymentStatus,
    ExtractedField,
    ExtractionResult,
    FieldProvenance,
    ResidencyStatus,
)

logger = logging.getLogger(__name__)

# ``[\s_]*`` so both "Annual Income:" and "annual_income:" are recognised.
SEP = r"[\s_]*"
ASSIGN = r"\s*[:=\-]\s*"
MONEY = r"([0-9][0-9,\.]*)"
INTEGER = r"(\d{1,4})"
BOOLEAN = r"(yes|no|true|false|y|n|none|n/a)"

TRUE_WORDS = {"yes", "true", "y", "1"}
FALSE_WORDS = {"no", "false", "n", "0", "none", "n/a"}


@dataclass(frozen=True)
class FieldPattern:
    field_name: str
    pattern: str
    kind: str
    confidence: float = 0.92

    def compiled(self) -> "re.Pattern[str]":
        return re.compile(self.pattern, re.IGNORECASE)


PATTERNS: Tuple[FieldPattern, ...] = (
    FieldPattern(
        "applicant_reference",
        rf"applicant{SEP}(?:reference|ref|id){ASSIGN}([A-Za-z0-9\-_/]+)",
        "text",
        0.98,
    ),
    FieldPattern(
        "full_name",
        rf"(?:full{SEP}name|applicant{SEP}name|name){ASSIGN}(.+)",
        "text",
        0.7,
    ),
    # \b so "Mortgage:" or "page:" can never be read as an age.
    FieldPattern("age", rf"(?:applicant{SEP})?\bage{ASSIGN}{INTEGER}", "int"),
    FieldPattern(
        "residency_status",
        rf"residency(?:{SEP}status)?{ASSIGN}([A-Za-z \-_]+)",
        "residency",
    ),
    FieldPattern(
        "employment_status",
        rf"employment(?:{SEP}status)?{ASSIGN}([A-Za-z \-_]+)",
        "employment",
    ),
    FieldPattern(
        "employment_months",
        rf"(?:months{SEP}in{SEP}current{SEP}employment|employment{SEP}months|"
        rf"months{SEP}employed|time{SEP}in{SEP}(?:current{SEP})?(?:employment|job)"
        rf"(?:{SEP}months)?){ASSIGN}{INTEGER}",
        "int",
    ),
    FieldPattern(
        "annual_income",
        rf"(?:gross{SEP})?annual{SEP}income{ASSIGN}{MONEY}",
        "money",
    ),
    FieldPattern(
        "other_annual_income",
        rf"other{SEP}annual{SEP}income{ASSIGN}{MONEY}",
        "money",
    ),
    FieldPattern(
        "existing_monthly_debt",
        rf"(?:existing{SEP}monthly{SEP}debt(?:{SEP}repayments)?|"
        rf"monthly{SEP}(?:debt|commitments|obligations)(?:{SEP}repayments)?)"
        rf"{ASSIGN}{MONEY}",
        "money",
    ),
    FieldPattern(
        "credit_score",
        rf"(?:internal{SEP})?credit{SEP}score{ASSIGN}(\d{{3}})",
        "int",
        0.96,
    ),
    FieldPattern(
        "credit_history_months",
        rf"credit{SEP}history(?:{SEP}months|\s*\(months\))?{ASSIGN}{INTEGER}",
        "int",
    ),
    FieldPattern(
        "active_defaults",
        rf"active{SEP}defaults?{ASSIGN}{INTEGER}",
        "int",
    ),
    FieldPattern(
        "has_prior_bankruptcy",
        rf"(?:prior{SEP})?bankruptcy(?:{SEP}recorded)?{ASSIGN}{BOOLEAN}",
        "bool",
    ),
    FieldPattern(
        "years_since_bankruptcy",
        rf"years{SEP}since{SEP}(?:bankruptcy|discharge){ASSIGN}([0-9]+(?:\.[0-9]+)?)",
        "float",
    ),
    FieldPattern(
        "liquid_savings",
        rf"(?:liquid{SEP}savings|savings{SEP}balance|reserves|savings){ASSIGN}{MONEY}",
        "money",
    ),
    FieldPattern(
        "documents_verified",
        rf"documents?{SEP}verified{ASSIGN}{BOOLEAN}",
        "bool",
    ),
    FieldPattern(
        "requested_amount",
        rf"requested{SEP}(?:loan{SEP})?amount{ASSIGN}{MONEY}",
        "money",
        0.96,
    ),
    FieldPattern(
        "loan_term_months",
        rf"(?:requested{SEP})?(?:loan{SEP})?term(?:{SEP}months|\s*\(months\))?"
        rf"{ASSIGN}{INTEGER}",
        "int",
    ),
    FieldPattern("property_value", rf"property{SEP}value{ASSIGN}{MONEY}", "money"),
    FieldPattern("vehicle_value", rf"vehicle{SEP}value{ASSIGN}{MONEY}", "money"),
    FieldPattern(
        "down_payment",
        rf"(?:down{SEP}payment|deposit(?:{SEP}amount)?){ASSIGN}{MONEY}",
        "money",
    ),
    FieldPattern(
        "is_first_time_buyer",
        rf"first[\s_-]*time{SEP}buyer{ASSIGN}{BOOLEAN}",
        "bool",
    ),
)

REQUIRED_FIELDS = (
    "age",
    "residency_status",
    "employment_status",
    "employment_months",
    "annual_income",
    "existing_monthly_debt",
    "credit_score",
    "requested_amount",
)


def _parse_money(raw: str) -> Optional[float]:
    cleaned = re.sub(r"[^0-9\.]", "", raw.replace(",", ""))
    if not cleaned or cleaned == ".":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_bool(raw: str) -> Optional[bool]:
    token = raw.strip().lower()
    if token in TRUE_WORDS:
        return True
    if token in FALSE_WORDS:
        return False
    return None


def _normalise_enum_token(raw: str) -> str:
    token = raw.strip().strip(".,;").upper()
    token = re.sub(r"[\s\-]+", "_", token)
    return token


def _parse_employment(raw: str) -> Optional[EmploymentStatus]:
    token = _normalise_enum_token(raw)
    aliases = {
        "FULLTIME": "FULL_TIME",
        "PARTTIME": "PART_TIME",
        "SELFEMPLOYED": "SELF_EMPLOYED",
        "SELF_EMPLOYMENT": "SELF_EMPLOYED",
        "CONTRACTOR": "CONTRACT",
        "EMPLOYED": "FULL_TIME",
    }
    token = aliases.get(token, token)
    try:
        return EmploymentStatus(token)
    except ValueError:
        return None


def _parse_residency(raw: str) -> Optional[ResidencyStatus]:
    token = _normalise_enum_token(raw)
    aliases = {
        "PERMANENT_RESIDENCY": "PERMANENT_RESIDENT",
        "PR": "PERMANENT_RESIDENT",
        "LONG_TERM_VISA_HOLDER": "LONG_TERM_VISA",
        "SHORT_TERM_VISA_HOLDER": "SHORT_TERM_VISA",
        "NATIONAL": "CITIZEN",
    }
    token = aliases.get(token, token)
    try:
        return ResidencyStatus(token)
    except ValueError:
        return None


def _coerce(kind: str, raw: str) -> Any:
    raw = raw.strip()
    if kind == "text":
        return raw.strip().strip(".,;")
    if kind == "int":
        try:
            return int(raw)
        except ValueError:
            return None
    if kind == "float":
        try:
            return float(raw)
        except ValueError:
            return None
    if kind == "money":
        return _parse_money(raw)
    if kind == "bool":
        return _parse_bool(raw)
    if kind == "employment":
        return _parse_employment(raw)
    if kind == "residency":
        return _parse_residency(raw)
    return raw


class ApplicantExtractor:
    """Extracts :class:`ApplicantProfile` fields from a parsed document."""

    def __init__(self, patterns: Tuple[FieldPattern, ...] = PATTERNS) -> None:
        self.patterns = patterns

    def extract(
        self,
        document: ParsedDocument,
        base: Optional[ApplicantProfile] = None,
    ) -> ExtractionResult:
        values: Dict[str, Any] = {}
        fields: List[ExtractedField] = []
        warnings: List[str] = list(document.warnings)

        for spec in self.patterns:
            regex = spec.compiled()
            found = None
            for page_number, page_text in document.pages:
                match = regex.search(page_text)
                if not match:
                    continue
                raw = match.group(1)
                value = _coerce(spec.kind, raw)
                if value is None or value == "":
                    warnings.append(
                        f"Found '{spec.field_name}' on page {page_number} but could not "
                        f"interpret the value '{raw.strip()[:40]}'."
                    )
                    continue
                line = _matched_line(page_text, match.start())
                found = ExtractedField(
                    field_name=spec.field_name,
                    value=getattr(value, "value", value),
                    confidence=spec.confidence,
                    provenance=FieldProvenance(
                        source_document=document.name,
                        page=page_number,
                        snippet=line[:180],
                        pattern=spec.field_name,
                    ),
                )
                values[spec.field_name] = value
                break
            if found is not None:
                fields.append(found)

        applicant, model_warnings = self._build_profile(values, base)
        warnings.extend(model_warnings)

        missing = [name for name in REQUIRED_FIELDS if getattr(applicant, name, None) is None]

        return ExtractionResult(
            source_document=document.name,
            page_count=document.page_count,
            character_count=document.character_count,
            parser=document.parser,
            applicant=applicant,
            fields=fields,
            missing_fields=missing,
            warnings=warnings,
        )

    @staticmethod
    def _build_profile(
        values: Dict[str, Any], base: Optional[ApplicantProfile]
    ) -> Tuple[ApplicantProfile, List[str]]:
        warnings: List[str] = []
        payload: Dict[str, Any] = {}
        if base is not None:
            payload.update(
                {
                    key: value
                    for key, value in base.model_dump(exclude_none=True).items()
                    if key != "notes"
                }
            )
        payload.update({key: value for key, value in values.items() if value is not None})
        payload.pop("notes", None)

        while True:
            try:
                return ApplicantProfile(**payload), warnings
            except ValidationError as exc:
                removed = False
                for error in exc.errors():
                    location = error.get("loc") or ()
                    if not location:
                        continue
                    key = str(location[0])
                    if key in payload:
                        warnings.append(
                            f"Ignored extracted value for '{key}': {error.get('msg')}"
                        )
                        payload.pop(key)
                        removed = True
                if not removed:
                    warnings.append(
                        "Extraction produced values that could not be validated; "
                        "returning an empty profile."
                    )
                    return ApplicantProfile(), warnings


def _matched_line(text: str, position: int) -> str:
    start = text.rfind("\n", 0, position) + 1
    end = text.find("\n", position)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def merge_profiles(
    base: Optional[ApplicantProfile], overlay: Optional[ApplicantProfile]
) -> ApplicantProfile:
    """Overlay wins for any field it sets; used to combine form input + documents."""
    if base is None:
        return overlay or ApplicantProfile()
    if overlay is None:
        return base
    payload = base.model_dump()
    payload.update(overlay.model_dump(exclude_none=True))
    return ApplicantProfile(**payload)


def extract_from_bytes(
    name: str, data: bytes, base: Optional[ApplicantProfile] = None
) -> ExtractionResult:
    return ApplicantExtractor().extract(parse_bytes(name, data), base=base)


def extract_from_path(
    path: Path, base: Optional[ApplicantProfile] = None
) -> ExtractionResult:
    return ApplicantExtractor().extract(parse_path(Path(path)), base=base)
