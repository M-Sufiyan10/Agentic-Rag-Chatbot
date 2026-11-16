"""Tests for the optional Langfuse client wrapper."""

import itertools
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from app.config import Settings
from app.observability import LangfuseTracing, build_tracing, trace_id_for_turn


def _settings(test_settings: Settings, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "langfuse_public_key": f"pk-{uuid.uuid4().hex}",
        "langfuse_secret_key": SecretStr("sk-test"),
        **overrides,
    }
    return test_settings.model_copy(update=values)


def test_trace_id_is_the_turn_uuid_hex() -> None:
    turn_id = str(uuid.uuid4())
    trace_id = trace_id_for_turn(turn_id)
    assert trace_id == uuid.UUID(turn_id).hex
    assert len(trace_id) == 32 and trace_id == trace_id.lower()
    with pytest.raises(ValueError):
        trace_id_for_turn("not-a-uuid")


def test_build_tracing_needs_both_keys(test_settings: Settings) -> None:
    none = {"langfuse_public_key": None, "langfuse_secret_key": None}
    assert build_tracing(test_settings.model_copy(update=none)) is None
    only_public = {"langfuse_public_key": "pk-only-public", "langfuse_secret_key": None}
    assert build_tracing(test_settings.model_copy(update=only_public)) is None
    only_secret = {"langfuse_public_key": None, "langfuse_secret_key": SecretStr("sk")}
    assert build_tracing(test_settings.model_copy(update=only_secret)) is None
    tracing = build_tracing(_settings(test_settings, langfuse_base_url="http://127.0.0.1:9"))
    assert isinstance(tracing, LangfuseTracing)
    tracing.shutdown()


def test_callback_traces_into_the_chosen_trace(test_settings: Settings) -> None:
    exporter = InMemorySpanExporter()
    tracing = LangfuseTracing(
        _settings(test_settings, langfuse_base_url="http://127.0.0.1:9"),
        span_exporter=exporter,
    )
    tid = trace_id_for_turn(str(uuid.uuid4()))
    model = GenericFakeChatModel(messages=itertools.repeat(AIMessage("hi")))
    model.invoke(
        "hello",
        config={
            "callbacks": tracing.callbacks(tid),
            "metadata": {"langfuse_user_id": "u1", "langfuse_session_id": "s1"},
            "run_name": "chat-turn",
        },
    )
    tracing.shutdown()
    spans = exporter.get_finished_spans()
    assert spans
    assert all(span.context is not None and span.context.trace_id == int(tid, 16) for span in spans)
    values = {str(v) for span in spans for v in (span.attributes or {}).values()}
    assert any("u1" in v for v in values)
    assert any("s1" in v for v in values)


async def test_unreachable_langfuse_reports_unavailable_quickly(test_settings: Settings) -> None:
    tracing = LangfuseTracing(
        _settings(
            test_settings, langfuse_base_url="http://127.0.0.1:9", langfuse_timeout_seconds=1
        ),
        span_exporter=InMemorySpanExporter(),
    )
    started = time.monotonic()
    assert await tracing.check() == "unavailable"
    assert time.monotonic() - started < 3
    assert tracing.status == "unavailable"
    tracing.shutdown()


async def test_feedback_calls_never_raise(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracing = LangfuseTracing(
        _settings(test_settings, langfuse_base_url="http://127.0.0.1:9"),
        span_exporter=InMemorySpanExporter(),
    )

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("langfuse is down")

    monkeypatch.setattr(tracing._client, "create_score", boom)
    monkeypatch.setattr(tracing._client.api.scores, "delete", boom)
    turn_id = str(uuid.uuid4())
    assert (
        await tracing.record_feedback(
            turn_id=turn_id, positive=True, first_voted_at=datetime.now(UTC)
        )
        is None
    )
    assert await tracing.delete_feedback(turn_id=turn_id) is None
    tracing.shutdown()


def test_tests_run_without_tracing(test_settings: Settings) -> None:
    assert build_tracing(test_settings) is None


def test_empty_keys_count_as_unset(test_settings: Settings) -> None:
    empty_public = {"langfuse_public_key": "", "langfuse_secret_key": SecretStr("sk")}
    empty_secret = {"langfuse_public_key": "pk-x", "langfuse_secret_key": SecretStr("")}
    assert build_tracing(test_settings.model_copy(update=empty_public)) is None
    assert build_tracing(test_settings.model_copy(update=empty_secret)) is None


async def test_feedback_score_contract(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracing = LangfuseTracing(
        _settings(test_settings, langfuse_base_url="http://127.0.0.1:9"),
        span_exporter=InMemorySpanExporter(),
    )
    scores: list[dict[str, Any]] = []
    deleted: list[str] = []
    monkeypatch.setattr(tracing._client, "create_score", lambda **kw: scores.append(kw))
    monkeypatch.setattr(
        tracing._client.api.scores, "delete", lambda score_id: deleted.append(score_id)
    )
    turn_id = str(uuid.uuid4())
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    base = {
        "name": "user_feedback",
        "data_type": "BOOLEAN",
        "trace_id": uuid.UUID(turn_id).hex,
        "score_id": f"feedback-{turn_id}",
        "timestamp": ts,
    }
    await tracing.record_feedback(turn_id=turn_id, positive=True, first_voted_at=ts)
    await tracing.record_feedback(turn_id=turn_id, positive=False, first_voted_at=ts)
    await tracing.delete_feedback(turn_id=turn_id)
    assert scores == [{**base, "value": 1}, {**base, "value": 0}]
    assert deleted == [f"feedback-{turn_id}"]
    tracing.shutdown()


async def test_delete_feedback_flushes_queued_scores_before_deleting(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tracing = LangfuseTracing(
        _settings(test_settings, langfuse_base_url="http://127.0.0.1:9"),
        span_exporter=InMemorySpanExporter(),
    )
    calls: list[str] = []
    monkeypatch.setattr(tracing._client, "flush", lambda: calls.append("flush"))
    monkeypatch.setattr(
        tracing._client.api.scores, "delete", lambda score_id: calls.append(f"delete:{score_id}")
    )
    turn_id = str(uuid.uuid4())
    await tracing.delete_feedback(turn_id=turn_id)
    assert calls == ["flush", f"delete:feedback-{turn_id}"]
    tracing.shutdown()
