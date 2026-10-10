"""Pluggable requirement-analysis provider used inside the analyst Agent.

The default implementation speaks the OpenAI-compatible HTTP API directly.
It deliberately has no LangChain dependency: retry counts, parsing and
validation are owned by this plugin and are visible in the audit trail.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Protocol

import requests

from api_agent.llm_client import llm_setting
from api_agent.llm_rules import LLMRuleSet


class RequirementAnalysisPlugin(Protocol):
    def analyze(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[LLMRuleSet, list[dict[str, object]]]:
        """Return validated rules and per-call retry records."""


class DirectLLMPlugin:
    """OpenAI-compatible HTTP implementation hosted by the analyst Agent."""

    structured_attempts = 4
    fallback_attempts = 3
    retry_backoff_seconds = 20

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 180,
    ) -> None:
        self.api_key = api_key
        self.endpoint = _chat_completions_url(base_url)
        self.model = model
        self.timeout = timeout

    def analyze(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[LLMRuleSet, list[dict[str, object]]]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        attempts: list[dict[str, object]] = []
        result: LLMRuleSet | None = None

        for attempt in range(1, self.structured_attempts + 1):
            record: dict[str, object] = {"phase": "structured", "attempt": attempt}
            try:
                raw = self._complete(messages, json_mode=True)
                result = LLMRuleSet.model_validate(_json_object(raw))
                record["status"] = "success"
                attempts.append(record)
                break
            except Exception as exc:  # noqa: BLE001 - captured for audit and retry
                record.update({"status": "failed", "reason": _error_reason(exc, self.api_key)})
                attempts.append(record)
                if attempt < self.structured_attempts:
                    time.sleep(self.retry_backoff_seconds * attempt)

        if result is None:
            for attempt in range(1, self.fallback_attempts + 1):
                record = {"phase": "text_json_fallback", "attempt": attempt}
                try:
                    raw = self._complete(
                        [
                            {
                                "role": "system",
                                "content": system_prompt + "\n只输出一个 JSON 对象，不要任何其他文字。",
                            },
                            {"role": "user", "content": user_prompt},
                        ],
                        json_mode=False,
                    )
                    result = LLMRuleSet.model_validate(_json_object(raw))
                    record["status"] = "success"
                    attempts.append(record)
                    break
                except Exception as exc:  # noqa: BLE001 - captured for audit and retry
                    record.update({"status": "failed", "reason": _error_reason(exc, self.api_key)})
                    attempts.append(record)
                    if attempt < self.fallback_attempts:
                        time.sleep(self.retry_backoff_seconds * attempt)

        if result is None:
            raise LLMAnalysisError(attempts)
        return result, attempts

    def _complete(self, messages: list[dict[str, str]], *, json_mode: bool) -> Any:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = requests.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") if isinstance(body, dict) else None
        if not choices:
            raise RuntimeError("LLM response did not contain choices")
        message = choices[0].get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        if content is None and isinstance(message, dict):
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                content = tool_calls[0].get("function", {}).get("arguments")
        if content is None:
            raise RuntimeError("LLM response did not contain message content")
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") for item in content if isinstance(item, dict)
            )
        return content


class LLMAnalysisError(RuntimeError):
    """Model analysis failed after all plugin-controlled attempts."""

    def __init__(self, attempts: list[dict[str, object]]):
        self.attempts = attempts
        detail = "; ".join(
            f"{item['phase']}#{item['attempt']}: {item.get('reason', 'unknown error')}"
            for item in attempts
            if item.get("status") == "failed"
        )
        message = f"LLM analysis failed after {len(attempts)} scripted attempts"
        if detail:
            message += f": {detail}"
        super().__init__(message)


def _chat_completions_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if not re.search(r"/v\d+$", value):
        value += "/v1"
    return value + "/chat/completions"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError("LLM response did not contain a JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM response JSON root was not an object")
    return parsed


def _error_reason(exc: Exception, api_key: str) -> str:
    detail = str(exc).strip() or "empty exception message"
    if api_key:
        detail = detail.replace(api_key, "[REDACTED]")
    return f"{type(exc).__name__}: {detail[:500]}"


def get_requirement_analysis_plugin() -> DirectLLMPlugin:
    return DirectLLMPlugin(
        api_key=llm_setting("LLM_API_KEY"),
        base_url=llm_setting("LLM_BASE_URL"),
        model=llm_setting("LLM_MODEL"),
    )
