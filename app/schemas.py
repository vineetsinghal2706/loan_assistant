"""Pydantic contracts shared by the API, rule engine, RAG layer and UI.

These models are the *only* structures crossing component boundaries, which
keeps the deterministic rule engine independent of FastAPI and of the LLM.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------------------------------------------------------
# Enumerations
# ----------------------------------------------------------------------------


class LoanProduct(str, Enum):
    PERSONAL_LOAN = "PERSONAL_LOAN"
    MORTGAGE = "MORTGAGE"
    AUTO_LOAN = "AUTO_LOAN"


class EmploymentStatus(str, Enum):
    FULL_TIME = "FULL_TIME"
    PART_TIME = "PART_TIME"
    SELF_EMPLOYED = "SELF_EMPLOYED"
    CONTRACT = "CONTRACT"
    RETIRED = "RETIRED"
    UNEMPLOYED = "UNEMPLOYED"
    STUDENT = "STUDENT"


class ResidencyStatus(str, Enum):
    CITIZEN = "CITIZEN"
    PERMANENT_RESIDENT = "PERMANENT_RESIDENT"
    LONG_TERM_VISA = "LONG_TERM_VISA"
    SHORT_TERM_VISA = "SHORT_TERM_VISA"
    NON_RESIDENT = "NON_RESIDENT"


class RuleSeverity(str, Enum):
    HARD = "HARD"  # failure => not eligible
    SOFT = "SOFT"  # failure => eligible with conditions / manual referral


class RuleStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"  # required applicant data missing
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EligibilityOutcome(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    ELIGIBLE_WITH_CONDITIONS = "ELIGIBLE_WITH_CONDITIONS"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


class ExplanationMode(str, Enum):
    CLAUDE = "claude"
    DETERMINISTIC_TEMPLATE = "deterministic_template"


# ----------------------------------------------------------------------------
# Applicant
# ----------------------------------------------------------------------------


class ApplicantProfile(BaseModel):
    """Synthetic applicant facts. No real customer data is ever used."""

    model_config = ConfigDict(extra="forbid")

    applicant_reference: str = Field(
        default="DEMO-APPLICANT",
        description="Pseudonymous reference used in audit records.",
    )
    full_name: Optional[str] = Field(default=None, description="Synthetic name only.")
    age: Optional[int] = Field(default=None, ge=0, le=120)
    residency_status: Optional[ResidencyStatus] = None

    employment_status: Optional[EmploymentStatus] = None
    employment_months: Optional[int] = Field(default=None, ge=0, le=900)
    annual_income: Optional[float] = Field(default=None, ge=0)
    other_annual_income: float = Field(default=0.0, ge=0)

    existing_monthly_debt: Optional[float] = Field(default=None, ge=0)
    credit_score: Optional[int] = Field(default=None, ge=300, le=900)
    credit_history_months: Optional[int] = Field(default=None, ge=0, le=900)
    active_defaults: Optional[int] = Field(default=None, ge=0)
    has_prior_bankruptcy: Optional[bool] = None
    years_since_bankruptcy: Optional[float] = Field(default=None, ge=0)
    liquid_savings: Optional[float] = Field(default=None, ge=0)
    documents_verified: Optional[bool] = None
    is_first_time_buyer: Optional[bool] = None

    requested_amount: Optional[float] = Field(default=None, ge=0)
    loan_term_months: Optional[int] = Field(default=None, ge=1, le=480)
    property_value: Optional[float] = Field(default=None, ge=0)
    vehicle_value: Optional[float] = Field(default=None, ge=0)
    down_payment: Optional[float] = Field(default=None, ge=0)

    notes: Optional[str] = None


class FieldProvenance(BaseModel):
    """Where an extracted value came from, so extraction stays auditable."""

    source_document: str
    page: Optional[int] = None
    snippet: Optional[str] = None
    pattern: Optional[str] = None


class ExtractedField(BaseModel):
    field_name: str
    value: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    provenance: Optional[FieldProvenance] = None


class ExtractionResult(BaseModel):
    source_document: str
    page_count: int = 0
    character_count: int = 0
    parser: str = "text"
    applicant: ApplicantProfile
    fields: List[ExtractedField] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Policy / RAG
# ----------------------------------------------------------------------------


class PolicyReference(BaseModel):
    """Pointer from a rule to the policy clause that authorises it."""

    document_id: str
    document_title: Optional[str] = None
    section: str
    policy_version: str
    quote: Optional[str] = None


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    section: str
    policy_version: str
    text: str
    score: float = 0.0
    source_path: Optional[str] = None


class Citation(BaseModel):
    """Citation surfaced to the end user and stored in the audit log."""

    marker: str = Field(description="Inline marker such as [1].")
    document_id: str
    document_title: str
    section: str
    policy_version: str
    quote: str
    rule_ids: List[str] = Field(default_factory=list)
    score: Optional[float] = None
    source_path: Optional[str] = None


class PolicyVersionInfo(BaseModel):
    policy_version: str
    title: str
    status: str
    effective_from: date
    effective_to: Optional[date] = None
    summary: str = ""
    change_log: List[str] = Field(default_factory=list)
    products: List[str] = Field(default_factory=list)
    rule_count: int = 0
    documents: List[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Rule engine output
# ----------------------------------------------------------------------------


class RuleResult(BaseModel):
    rule_id: str
    title: str
    category: str
    severity: RuleSeverity
    status: RuleStatus
    detail: str = ""
    message: str = ""
    missing_fields: List[str] = Field(default_factory=list)
    policy_reference: PolicyReference
    observed: Dict[str, Any] = Field(default_factory=dict)
    threshold: Optional[Any] = None


class EligibilityDecision(BaseModel):
    """The deterministic source of truth. Claude may never alter this."""

    decision_id: str
    outcome: EligibilityOutcome
    headline: str
    product: LoanProduct
    policy_version: str
    policy_effective_from: date
    policy_effective_to: Optional[date] = None
    engine_version: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    rule_results: List[RuleResult] = Field(default_factory=list)
    failed_hard_rules: List[str] = Field(default_factory=list)
    failed_soft_rules: List[str] = Field(default_factory=list)
    unknown_rules: List[str] = Field(default_factory=list)

    computed_facts: Dict[str, Any] = Field(default_factory=dict)
    estimated_monthly_payment: Optional[float] = None
    max_eligible_amount: Optional[float] = None
    requested_amount: Optional[float] = None
    required_actions: List[str] = Field(default_factory=list)
    decision_hash: str = ""

    @property
    def is_eligible(self) -> bool:
        return self.outcome in {
            EligibilityOutcome.ELIGIBLE,
            EligibilityOutcome.ELIGIBLE_WITH_CONDITIONS,
        }


# ----------------------------------------------------------------------------
# API requests / responses
# ----------------------------------------------------------------------------


class EligibilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant: ApplicantProfile
    product: LoanProduct = LoanProduct.PERSONAL_LOAN
    policy_version: Optional[str] = Field(
        default=None, description="Explicit policy version, e.g. '1.0'. Overrides as_of_date."
    )
    as_of_date: Optional[date] = Field(
        default=None, description="Resolve the policy version in force on this date."
    )
    question: Optional[str] = Field(
        default=None, description="Optional applicant question used to steer retrieval."
    )


class EligibilityResponse(BaseModel):
    decision: EligibilityDecision
    citations: List[Citation] = Field(default_factory=list)
    retrieved_chunks: int = 0
    audit_id: Optional[int] = None
    trace_id: Optional[str] = None
    disclaimer: str = (
        "Educational demonstration using synthetic DemoBank policies. "
        "Not a real credit decision and not financial advice."
    )


class VersionComparisonEntry(BaseModel):
    policy_version: str
    outcome: EligibilityOutcome
    headline: str
    failed_hard_rules: List[str] = Field(default_factory=list)
    failed_soft_rules: List[str] = Field(default_factory=list)
    unknown_rules: List[str] = Field(default_factory=list)
    max_eligible_amount: Optional[float] = None
    decision_hash: str = ""


class VersionComparisonResponse(BaseModel):
    product: LoanProduct
    entries: List[VersionComparisonEntry]
    differences: List[str] = Field(default_factory=list)
    identical: bool = False


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applicant: ApplicantProfile
    product: LoanProduct = LoanProduct.PERSONAL_LOAN
    policy_version: Optional[str] = None
    as_of_date: Optional[date] = None
    question: Optional[str] = None
    history: List[ChatTurn] = Field(default_factory=list)
    stream_tokens: bool = True


class PolicySearchResult(BaseModel):
    query: str
    policy_version: str
    chunks: List[RetrievedChunk] = Field(default_factory=list)


class AuditRecordOut(BaseModel):
    id: int
    trace_id: str
    created_at: datetime
    applicant_reference: str
    applicant_fingerprint: str
    product: str
    policy_version: str
    outcome: str
    decision_hash: str
    engine_version: str
    explanation_mode: str
    llm_model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    guardrail_violations: List[str] = Field(default_factory=list)
    rule_results: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    computed_facts: Dict[str, Any] = Field(default_factory=dict)
    explanation: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    app_name: str
    app_env: str
    version: str
    engine_version: str
    default_policy_version: str
    policy_versions: List[str] = Field(default_factory=list)
    llm_mode: ExplanationMode
    embedding_backend: str
    vector_backend: str
    indexed_chunks: int = 0
    synthetic_data_notice: str = (
        "DEMO BANK - SYNTHETIC DOCUMENT - FOR EDUCATIONAL USE ONLY"
    )
