"""Claude streaming client with an offline deterministic fallback.

When ``ANTHROPIC_API_KEY`` is set and ``ENABLE_LLM`` is true, explanations are
streamed from the Anthropic Messages API. Otherwise - and on any API error -
the assistant streams the deterministic template explanation instead, so the
demo, the test suite and CI behave identically without network access.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable, List, Mapping, Optional, Sequence

from app.config import Settings, get_settings
from app.llm.prompts import (
    SYSTEM_PROMPT,
    build_explanation_prompt,
    render_deterministic_explanation,
)
from app.schemas import Citation, EligibilityDecision, ExplanationMode

logger = logging.getLogger(__name__)

TOKENISER = re.compile(r"\S+\s*")
MOCK_CHUNK_WORDS = 6
MOCK_CHUNK_DELAY_SECONDS = 0.01


@dataclass
class StreamOutcome:
    """Mutable record of what happened during one streamed explanation."""

    mode: ExplanationMode = ExplanationMode.DETERMINISTIC_TEMPLATE
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_seconds: float = 0.0
    text: str = ""
    error: Optional[str] = None
    fell_back: bool = False
    notes: List[str] = field(default_factory=list)


def _chunk_text(text: str, words_per_chunk: int = MOCK_CHUNK_WORDS) -> List[str]:
    tokens = TOKENISER.findall(text)
    if not tokens:
        return []
    return [
        "".join(tokens[index : index + words_per_chunk])
        for index in range(0, len(tokens), words_per_chunk)
    ]


class ClaudeExplainer:
    """Streams the natural-language explanation of a deterministic decision."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._client = None
        if self.settings.llm_enabled:
            try:
                from anthropic import AsyncAnthropic  # noqa: PLC0415

                self._client = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
            except Exception as exc:  # pragma: no cover - environment dependent
                logger.warning(
                    "Anthropic SDK unavailable (%s); using the deterministic explainer.", exc
                )
                self._client = None

    # ------------------------------------------------------------------
    @property
    def mode(self) -> ExplanationMode:
        return (
            ExplanationMode.CLAUDE
            if self._client is not None
            else ExplanationMode.DETERMINISTIC_TEMPLATE
        )

    @property
    def model(self) -> Optional[str]:
        return self.settings.claude_model if self._client is not None else None

    def build_prompt(
        self,
        decision: EligibilityDecision,
        citations: Sequence[Citation],
        question: Optional[str] = None,
        history: Optional[Iterable[Mapping[str, str]]] = None,
    ) -> str:
        return build_explanation_prompt(decision, citations, question=question, history=history)

    # ------------------------------------------------------------------
    async def stream(
        self,
        decision: EligibilityDecision,
        citations: Sequence[Citation],
        question: Optional[str] = None,
        history: Optional[Iterable[Mapping[str, str]]] = None,
        outcome: Optional[StreamOutcome] = None,
    ) -> AsyncIterator[str]:
        """Yield explanation text chunks. Populates ``outcome`` as it goes."""

        record = outcome if outcome is not None else StreamOutcome()
        record.mode = self.mode
        record.model = self.model
        started = time.perf_counter()
        fallback = render_deterministic_explanation(decision, citations)

        if self._client is None:
            async for chunk in self._stream_template(fallback, record):
                yield chunk
            record.latency_seconds = time.perf_counter() - started
            return

        prompt = self.build_prompt(decision, citations, question=question, history=history)
        emitted = 0
        try:
            async with self._client.messages.stream(
                model=self.settings.claude_model,
                max_tokens=self.settings.claude_max_tokens,
                temperature=self.settings.claude_temperature,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for piece in stream.text_stream:
                    if not piece:
                        continue
                    emitted += 1
                    record.text += piece
                    yield piece
                final = await stream.get_final_message()
                usage = getattr(final, "usage", None)
                if usage is not None:
                    record.input_tokens = getattr(usage, "input_tokens", None)
                    record.output_tokens = getattr(usage, "output_tokens", None)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Claude streaming failed (%s); falling back to template.", exc)
            record.error = str(exc)
            record.fell_back = True
            record.mode = ExplanationMode.DETERMINISTIC_TEMPLATE
            if emitted == 0:
                record.text = ""
                async for chunk in self._stream_template(fallback, record):
                    yield chunk
            else:
                notice = (
                    "\n\n[The explanation service was interrupted. The deterministic "
                    "summary below is authoritative.]\n\n"
                )
                record.text += notice
                yield notice
                async for chunk in self._stream_template(fallback, record):
                    yield chunk

        record.latency_seconds = time.perf_counter() - started

    async def _stream_template(
        self, text: str, record: StreamOutcome
    ) -> AsyncIterator[str]:
        for chunk in _chunk_text(text):
            record.text += chunk
            yield chunk
            if MOCK_CHUNK_DELAY_SECONDS:
                await asyncio.sleep(MOCK_CHUNK_DELAY_SECONDS)

    # ------------------------------------------------------------------
    async def explain(
        self,
        decision: EligibilityDecision,
        citations: Sequence[Citation],
        question: Optional[str] = None,
        history: Optional[Iterable[Mapping[str, str]]] = None,
    ) -> StreamOutcome:
        """Non-streaming convenience wrapper (used by evals and tests)."""
        record = StreamOutcome()
        async for _ in self.stream(
            decision, citations, question=question, history=history, outcome=record
        ):
            pass
        return record

    def explain_sync(
        self,
        decision: EligibilityDecision,
        citations: Sequence[Citation],
        question: Optional[str] = None,
    ) -> StreamOutcome:
        return asyncio.run(self.explain(decision, citations, question=question))


_explainer: Optional[ClaudeExplainer] = None


def get_explainer() -> ClaudeExplainer:
    global _explainer
    if _explainer is None:
        _explainer = ClaudeExplainer()
    return _explainer


def reset_explainer() -> None:
    global _explainer
    _explainer = None
