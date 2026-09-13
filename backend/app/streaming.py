import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Optional

from .llm.client import LLMClient
from .metrics import STREAM_ERRORS_TOTAL, TOKENS_STREAMED_TOTAL

logger = logging.getLogger("loan_assistant.streaming")


async def sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def stream_chat_response(
    llm: LLMClient,
    question: str,
    rule_version: str,
    decision: Optional[Any],
    chunks: list[dict],
    queue_maxsize: int = 32,
) -> AsyncGenerator[str, None]:
    """Bridges the LLM token producer and the HTTP consumer through a bounded
    asyncio.Queue. If the client reads slowly, `queue.put` blocks, which
    applies backpressure all the way back to token generation instead of the
    server buffering an unbounded amount of text in memory.

    Mid-stream provider failures (timeouts, API errors) are caught in the
    producer and surfaced to the client as a single SSE "error" event instead
    of crashing the connection or hanging it open.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
    sentinel = object()

    async def producer():
        try:
            async for token in llm.stream_answer(question, rule_version, decision, chunks):
                await queue.put(token)  # blocks if the consumer is slow
            await queue.put(sentinel)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any provider failure
            logger.exception("LLM streaming failed mid-stream")
            STREAM_ERRORS_TOTAL.inc()
            await queue.put(("__error__", str(exc)))
            await queue.put(sentinel)

    producer_task = asyncio.create_task(producer())
    full_answer: list[str] = []
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            if isinstance(item, tuple) and item and item[0] == "__error__":
                yield await sse_event("error", {"message": item[1]})
                break
            full_answer.append(item)
            TOKENS_STREAMED_TOTAL.inc()
            yield await sse_event("token", {"text": item})
    finally:
        if not producer_task.done():
            producer_task.cancel()

    yield await sse_event(
        "done",
        {
            "answer": "".join(full_answer),
            "rule_version": rule_version,
            "decision": decision.model_dump() if decision else None,
            "citations": chunks,
        },
    )
