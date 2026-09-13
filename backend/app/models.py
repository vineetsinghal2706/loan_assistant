from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel


class ApplicantProfile(BaseModel):
    monthly_income: float
    monthly_debt: float
    credit_score: int
    age: int
    employment_type: Literal["salaried", "self_employed", "unemployed"]
    requested_amount: float
    loan_tenure_months: int


class ChatRequest(BaseModel):
    session_id: str
    message: str
    applicant: Optional[ApplicantProfile] = None


class EligibilityDecision(BaseModel):
    outcome: Literal["eligible", "not_eligible", "needs_review"]
    reasons: list[str]
    rule_ids: list[str]
    rule_version: str


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[dict[str, Any]]
    decision: Optional[EligibilityDecision] = None
    rule_version: str
    latency_ms: float


class AuditRecord(BaseModel):
    timestamp: datetime
    session_id: str
    request_id: str
    question: str
    rule_version: str
    decision: Optional[EligibilityDecision] = None
    citations: list[dict[str, Any]]
    latency_ms: float
    error: Optional[str] = None
