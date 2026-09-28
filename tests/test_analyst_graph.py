"""Real LangGraph retries/fallbacks with fake model I/O (no API keys/network)."""

from types import SimpleNamespace

import httpx
import pytest
from langgraph.types import RetryPolicy
from openai import AuthenticationError

from api_agent.analyst_graph import MissingStructuredOutput, build_analyst_graph, retryable_llm_error
from api_agent.llm_rules import LLMRule, LLMRuleSet, LLMSetupStep


POLICY = RetryPolicy(initial_interval=0, jitter=False, max_attempts=2, retry_on=retryable_llm_error)
MESSAGES = [{"role": "system", "content": "rules"}, {"role": "user", "content": "requirements"}]


class FakeModel:
    def __init__(self, structured, text=()):
        self.structured = iter(structured)
        self.text = iter(text)
        self.structured_calls = self.text_calls = 0

    def with_structured_output(self, schema, method):
        assert schema is LLMRuleSet and method == "function_calling"
        return SimpleNamespace(invoke=self.invoke_structured)

    def invoke_structured(self, messages):
        self.structured_calls += 1
        value = next(self.structured)
        if isinstance(value, Exception):
            raise value
        return value

    def invoke(self, messages):
        self.text_calls += 1
        value = next(self.text)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(content=value)


def test_structured_success_is_validated_and_resource_ids_repaired():
    rules = LLMRuleSet(rules=[LLMRule(
        rule_id="order", action="POST /api/orders", action_params={"product_id": 1},
        setup=[LLMSetupStep(action="create", resource="product", path="/api/products")],
    )])
    model = FakeModel([rules])
    result = build_analyst_graph(model, retry_policy=POLICY).invoke({"messages": MESSAGES})
    assert result["rules"]["rules"][0]["action_params"]["product_id"] == "{{product.id}}"
    assert len(result["repairs"]) == 1
    assert model.structured_calls == 1 and model.text_calls == 0
    assert rules.rules[0].action_params["product_id"] == 1  # no mutation of model response


def test_transient_error_uses_native_retry_before_fallback():
    model = FakeModel([TimeoutError("transient"), LLMRuleSet()])
    result = build_analyst_graph(model, retry_policy=POLICY).invoke({"messages": MESSAGES})
    assert result["extraction_method"] == "structured"
    assert model.structured_calls == 2 and model.text_calls == 0


def test_exhausted_structured_retries_route_to_text_json():
    model = FakeModel([None, None], ['```json\n{"rules": []}\n```'])
    result = build_analyst_graph(model, retry_policy=POLICY).invoke({"messages": MESSAGES})
    assert result["extraction_method"] == "text_json"
    assert model.structured_calls == 2 and model.text_calls == 1
    assert MESSAGES[0]["content"] == "rules"


def test_text_fallback_has_its_own_bounded_retry_policy():
    model = FakeModel([None, None], ["invalid", '{"rules": []}'])
    result = build_analyst_graph(model, retry_policy=POLICY).invoke({"messages": MESSAGES})
    assert result["rules"] == {"rules": []}
    assert model.structured_calls == model.text_calls == 2


def test_all_formats_failing_raise_after_the_configured_budget():
    model = FakeModel([None, None], ["invalid", "still invalid"])
    with pytest.raises(MissingStructuredOutput):
        build_analyst_graph(model, retry_policy=POLICY).invoke({"messages": MESSAGES})
    assert model.structured_calls == model.text_calls == 2


def test_auth_failure_does_not_retry_or_fall_back():
    response = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid"))
    model = FakeModel([AuthenticationError("invalid credentials", response=response, body={})])
    with pytest.raises(AuthenticationError):
        build_analyst_graph(model, retry_policy=POLICY).invoke({"messages": MESSAGES})
    assert model.structured_calls == 1 and model.text_calls == 0


def test_client_disables_nested_sdk_retries(monkeypatch):
    from api_agent.llm_client import get_chat_model

    for key, value in {"LLM_API_KEY": "fake-test-key", "LLM_BASE_URL": "https://example.invalid/v4", "LLM_MODEL": "fake"}.items():
        monkeypatch.setenv(key, value)
    model = get_chat_model()
    assert model.max_retries == 0
    assert str(model.openai_api_base) == "https://example.invalid/v4"
