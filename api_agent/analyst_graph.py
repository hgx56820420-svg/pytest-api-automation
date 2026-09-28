"""LLM extraction subgraph: native retry, structured/text fallback, validation."""

from typing import Any, Literal, TypedDict

import httpx
from langchain_core.exceptions import OutputParserException
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy
from openai import APIConnectionError, APIStatusError, BadRequestError
from pydantic import ValidationError

from api_agent.llm_rules import LLMRuleSet


class AnalystState(TypedDict, total=False):
    messages: list[dict[str, str]]
    rules: dict[str, Any]
    repairs: list[str]
    extraction_method: str


class MissingStructuredOutput(ValueError):
    """A gateway accepted tool mode but returned no usable tool result."""


def retryable_llm_error(exc: Exception) -> bool:
    """Retry transient transport failures and invalid model output only."""
    if isinstance(exc, APIStatusError):
        return exc.status_code in {408, 409, 429} or exc.status_code >= 500
    return isinstance(exc, (
        APIConnectionError, httpx.TransportError, TimeoutError,
        MissingStructuredOutput, OutputParserException, ValidationError,
    ))


def build_analyst_graph(model: Any, *, retry_policy: RetryPolicy | None = None):
    """Build a real subgraph; RetryPolicy owns all retry timing and counts.

    A supplied policy is useful for deployments and zero-delay test doubles.
    The HTTP client must disable its own retry loop to keep the budget exact.
    """
    structured_policy = retry_policy or RetryPolicy(
        initial_interval=2, backoff_factor=2, max_interval=20,
        max_attempts=4, retry_on=retryable_llm_error,
    )
    text_policy = retry_policy or structured_policy._replace(max_attempts=3)

    def structured(state: AnalystState) -> Command[Literal["repair_ids"]]:
        result = model.with_structured_output(LLMRuleSet, method="function_calling").invoke(state["messages"])
        if result is None:
            raise MissingStructuredOutput("Gateway returned no structured output")
        rules = LLMRuleSet.model_validate(result)
        return Command(update={"rules": rules.model_dump(), "extraction_method": "structured"}, goto="repair_ids")

    def fallback(state: AnalystState, error: NodeError) -> Command[Literal["text_json"]]:
        # Authentication, permission and programming errors are not repaired
        # by spending more requests in a different response format.
        if not (retryable_llm_error(error.error) or isinstance(error.error, BadRequestError)):
            raise error.error
        return Command(goto="text_json")

    def text_json(state: AnalystState) -> dict[str, Any]:
        messages = [dict(message) for message in state["messages"]]
        messages[0]["content"] += "\n只输出一个 JSON 对象，不要任何其他文字。"
        raw = model.invoke(messages)
        text = raw.content
        if not isinstance(text, str):
            raise MissingStructuredOutput("Gateway returned non-text JSON output")
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise MissingStructuredOutput("Gateway returned no JSON object")
        rules = LLMRuleSet.model_validate_json(text[start:end + 1])
        return {"rules": rules.model_dump(), "extraction_method": "text_json"}

    def repair_ids(state: AnalystState) -> dict[str, Any]:
        from api_agent.llm_analyst import repair_resource_ids

        rules = LLMRuleSet.model_validate(state["rules"])
        repairs = repair_resource_ids(rules)
        return {"rules": rules.model_dump(), "repairs": repairs}

    graph = StateGraph(AnalystState)
    graph.add_node("structured", structured, retry_policy=structured_policy, error_handler=fallback)
    graph.add_node("text_json", text_json, retry_policy=text_policy)
    graph.add_node("repair_ids", repair_ids)
    graph.add_edge(START, "structured")
    graph.add_edge("text_json", "repair_ids")
    graph.add_edge("repair_ids", END)
    return graph.compile(name="requirement-analyst")
