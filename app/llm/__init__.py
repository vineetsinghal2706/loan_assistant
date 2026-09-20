"""Claude explanation layer.

Claude answers only: *how should the deterministic result be explained?*
"""

from app.llm.claude_client import ClaudeExplainer, StreamOutcome, get_explainer
from app.llm.guardrails import GuardrailReport, validate_explanation
from app.llm.prompts import (
    SYSTEM_PROMPT,
    build_explanation_prompt,
    render_deterministic_explanation,
)

__all__ = [
    "ClaudeExplainer",
    "StreamOutcome",
    "get_explainer",
    "GuardrailReport",
    "validate_explanation",
    "SYSTEM_PROMPT",
    "build_explanation_prompt",
    "render_deterministic_explanation",
]
