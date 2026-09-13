import asyncio
from typing import AsyncGenerator, Optional

from .prompts import build_system_prompt, render_stub_answer


class LLMClient:
    """Thin streaming wrapper around either the real Anthropic API or a fully
    offline, deterministic stub. The stub exists so the whole pipeline (RAG +
    rules + streaming + audit logging + CI regression gate) can be exercised
    without any API key - flip LLM_PROVIDER=anthropic once you have one.
    """

    def __init__(self, provider: str, model: str, api_key: Optional[str]):
        self.provider = provider
        self.model = model
        self.api_key = api_key

    async def stream_answer(
        self,
        question: str,
        rule_version: str,
        decision: Optional[object],
        chunks: list[dict],
    ) -> AsyncGenerator[str, None]:
        if self.provider == "anthropic":
            async for token in self._stream_anthropic(question, rule_version, decision, chunks):
                yield token
        else:
            async for token in self._stream_stub(question, rule_version, decision, chunks):
                yield token

    async def _stream_anthropic(self, question, rule_version, decision, chunks):
        import anthropic  # imported lazily so the stub path has no hard dependency

        system_prompt = build_system_prompt(rule_version, decision, chunks)
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        async with client.messages.stream(
            model=self.model,
            max_tokens=600,
            system=system_prompt,
            messages=[{"role": "user", "content": question}],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def _stream_stub(self, question, rule_version, decision, chunks):
        answer = render_stub_answer(question, rule_version, decision, chunks)
        for word in answer.split(" "):
            await asyncio.sleep(0)  # yield control, simulating token-by-token streaming
            yield word + " "
