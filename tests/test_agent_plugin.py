"""Tests for the Agent-hosted direct LLM plugin and workflow cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_agent import llm_plugin
from api_agent.llm_plugin import DirectLLMPlugin, LLMAnalysisError
from api_agent.workflow_cache import WorkflowCache


class FakeResponse:
    def __init__(self, body: dict[str, object]):
        self.body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.body


def _response(content: object) -> FakeResponse:
    return FakeResponse({"choices": [{"message": {"content": content}}]})


def _plugin() -> DirectLLMPlugin:
    return DirectLLMPlugin(
        api_key="test-secret",
        base_url="https://gateway.example/v7",
        model="test-model",
        timeout=1,
    )


def test_chat_completions_url_supports_arbitrary_api_version() -> None:
    assert llm_plugin._chat_completions_url("https://gateway.example") == (
        "https://gateway.example/v1/chat/completions"
    )
    assert llm_plugin._chat_completions_url("https://gateway.example/v7") == (
        "https://gateway.example/v7/chat/completions"
    )
    assert llm_plugin._chat_completions_url(
        "https://gateway.example/v7/chat/completions"
    ) == "https://gateway.example/v7/chat/completions"


def test_direct_plugin_validates_structured_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        calls.append({"url": url, **kwargs})
        return _response(json.dumps({"rules": [{"rule_id": "REQ-001"}]}))

    monkeypatch.setattr(llm_plugin.requests, "post", fake_post)

    rules, attempts = _plugin().analyze("system", "user")

    assert [rule.rule_id for rule in rules.rules] == ["REQ-001"]
    assert attempts == [{"phase": "structured", "attempt": 1, "status": "success"}]
    assert calls[0]["url"] == "https://gateway.example/v7/chat/completions"
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_structured_failures_fall_back_to_text_json(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = [_response(json.dumps({"rules": "not-a-list"})) for _ in range(4)]
    responses.append(_response('prefix {"rules": [{"rule_id": "REQ-FALLBACK"}]} suffix'))
    calls = 0

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        nonlocal calls
        response = responses[calls]
        calls += 1
        return response

    monkeypatch.setattr(llm_plugin.requests, "post", fake_post)
    monkeypatch.setattr(llm_plugin.time, "sleep", lambda _: None)

    rules, attempts = _plugin().analyze("system", "user")

    assert rules.rules[0].rule_id == "REQ-FALLBACK"
    assert calls == 5
    assert len(attempts) == 5
    assert attempts[-1] == {
        "phase": "text_json_fallback",
        "attempt": 1,
        "status": "success",
    }


def test_exhausted_plugin_retries_keep_reason_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "test-secret"

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        raise RuntimeError(f"gateway rejected key {secret}")

    monkeypatch.setattr(llm_plugin.requests, "post", fake_post)
    monkeypatch.setattr(llm_plugin.time, "sleep", lambda _: None)

    with pytest.raises(LLMAnalysisError) as error:
        _plugin().analyze("system", "user")

    assert len(error.value.attempts) == 7
    assert all(item["status"] == "failed" for item in error.value.attempts)
    assert secret not in str(error.value)
    assert all(secret not in str(item["reason"]) for item in error.value.attempts)


def test_workflow_cache_requires_matching_key_and_existing_artifact(tmp_path: Path) -> None:
    cache = WorkflowCache(tmp_path)
    artifact = tmp_path / "artifact.json"
    key = cache.fingerprint({"input": "same"})

    assert not cache.hit("stage", key, [artifact])
    artifact.write_text("{}", encoding="utf-8")
    cache.store("stage", key, [artifact])
    assert cache.hit("stage", key, [artifact])
    assert not cache.hit("stage", cache.fingerprint({"input": "changed"}), [artifact])
    artifact.unlink()
    assert not cache.hit("stage", key, [artifact])
