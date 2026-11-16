"""Optional Langfuse tracing: per-turn callbacks and feedback scores.

Everything here is best-effort. Tracing being off or unreachable must never
break chat or feedback, so failures are logged at WARNING and swallowed.
"""

import asyncio
import logging
import math
import uuid
from datetime import datetime
from typing import Protocol

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from opentelemetry.sdk.trace.export import SpanExporter

from app.config import Settings

logger = logging.getLogger(__name__)

FEEDBACK_SCORE_NAME = "user_feedback"


def trace_id_for_turn(turn_id: str) -> str:
    """The Langfuse trace id of a turn: its uuid as 32 lowercase hex chars."""
    return uuid.UUID(turn_id).hex


class Tracing(Protocol):
    status: str  # "ok" | "unavailable"

    def callbacks(self, trace_id: str) -> list[BaseCallbackHandler]: ...

    async def check(self) -> str: ...

    async def record_feedback(
        self, *, turn_id: str, positive: bool, first_voted_at: datetime
    ) -> None: ...

    async def delete_feedback(self, *, turn_id: str) -> None: ...

    def shutdown(self) -> None: ...


class LangfuseTracing:
    def __init__(self, settings: Settings, *, span_exporter: SpanExporter | None = None) -> None:
        secret = settings.langfuse_secret_key
        if not settings.langfuse_public_key or secret is None or not secret.get_secret_value():
            raise ValueError("LangfuseTracing needs both Langfuse keys")
        self._timeout = settings.langfuse_timeout_seconds
        self._public_key = settings.langfuse_public_key
        self.status = "unavailable"
        self._client = Langfuse(
            public_key=self._public_key,
            secret_key=secret.get_secret_value(),
            base_url=settings.langfuse_base_url,
            timeout=max(1, math.ceil(self._timeout)),
            span_exporter=span_exporter,
        )

    def callbacks(self, trace_id: str) -> list[BaseCallbackHandler]:
        return [CallbackHandler(public_key=self._public_key, trace_context={"trace_id": trace_id})]

    async def check(self) -> str:
        try:
            ok = await asyncio.wait_for(asyncio.to_thread(self._client.auth_check), self._timeout)
            self.status = "ok" if ok else "unavailable"
        except Exception as exc:
            logger.warning("Langfuse is unreachable; tracing is unavailable: %r", exc)
            self.status = "unavailable"
        return self.status

    async def record_feedback(
        self, *, turn_id: str, positive: bool, first_voted_at: datetime
    ) -> None:
        try:
            self._client.create_score(
                name=FEEDBACK_SCORE_NAME,
                value=1 if positive else 0,
                trace_id=trace_id_for_turn(turn_id),
                score_id=f"feedback-{turn_id}",
                data_type="BOOLEAN",
                timestamp=first_voted_at,
            )
        except Exception as exc:
            logger.warning("Could not send feedback score to Langfuse: %r", exc)

    async def delete_feedback(self, *, turn_id: str) -> None:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._client.api.scores.delete, f"feedback-{turn_id}"),
                self._timeout,
            )
        except Exception as exc:
            logger.warning("Could not delete feedback score in Langfuse: %r", exc)

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception as exc:
            logger.warning("Langfuse shutdown failed: %r", exc)


def build_tracing(settings: Settings) -> LangfuseTracing | None:
    """A tracing client, or ``None`` unless both Langfuse keys are set."""
    secret = settings.langfuse_secret_key
    if not settings.langfuse_public_key or secret is None or not secret.get_secret_value():
        return None
    return LangfuseTracing(settings)
