"""Declarative database assertion DSL executor (deterministic).

四种断言原语，全部由 DatabaseObserver 的白名单查询执行：

- field_delta        字段值变化量：after == round(before + delta, 2)
- field_unchanged    字段值不变
- row_created        表新增一行（count_after == count_before + 1）
- row_count_unchanged 表行数不变

真实性约束：
- 行定位键只能来自资源池（setup/action 的真实响应），查不到就如实报
  ``inconclusive``，绝不编造 ID；
- 表名必须在 adapter 的可观察表白名单内，越界直接抛错。
"""

from __future__ import annotations

from typing import Any

from api_agent.llm_rules import LLMDBAssertion
from api_agent.models import AssertionResult


def resolve_placeholders(value: Any, resources: dict[str, Any]) -> Any:
    """Replace {{resource.field}} placeholders with real response values."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{{") and text.endswith("}}"):
            path = text[2:-2].strip().split(".")
            node: Any = resources
            for segment in path:
                if not isinstance(node, dict) or segment not in node:
                    return value  # 未解析的占位符保持原样，由上层如实报错
                node = node[segment]
            return node
        return value
    if isinstance(value, dict):
        return {key: resolve_placeholders(child, resources) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(child, resources) for child in value]
    return value


class DbAssertionEngine:
    """Capture before-state, run the action, then evaluate assertions."""

    def __init__(self, assertions: list[LLMDBAssertion], resources: dict[str, Any], observer):
        self.assertions = assertions
        self.resources = resources
        self.observer = observer

    def _key_id(self, assertion: LLMDBAssertion) -> Any:
        resource = self.resources.get(assertion.key_resource)
        if not isinstance(resource, dict) or "id" not in resource:
            return None
        return resource["id"]

    def _capture(self, assertion: LLMDBAssertion) -> dict[str, Any]:
        if assertion.kind in {"field_delta", "field_unchanged"}:
            key_id = self._key_id(assertion)
            if key_id is None:
                return {"error": f"resource '{assertion.key_resource}' has no real id"}
            try:
                return {"row": self.observer.snapshot_row(assertion.table, key_id)}
            except ValueError as exc:
                return {"error": str(exc)}
        try:
            return {"count": self.observer.row_count(assertion.table)}
        except ValueError as exc:
            return {"error": str(exc)}

    def capture_before(self) -> list[dict[str, Any]]:
        return [self._capture(assertion) for assertion in self.assertions]

    def evaluate(self, before_states: list[dict[str, Any]]) -> list[AssertionResult]:
        results: list[AssertionResult] = []
        for assertion, before in zip(self.assertions, before_states, strict=True):
            name = f"db:{assertion.kind}:{assertion.table}:{assertion.field or 'count'}"
            if "error" in before:
                results.append(
                    AssertionResult(name=name, status="inconclusive", expected=assertion.kind, actual="error", detail=before["error"])
                )
                continue
            if assertion.kind == "field_delta":
                after_row = self.observer.snapshot_row(assertion.table, self._key_id(assertion))
                expected = round((before["row"].get(assertion.field) or 0) + (assertion.delta or 0), 2)
                actual = after_row.get(assertion.field)
                results.append(
                    AssertionResult(name=name, status="passed" if actual == expected else "failed", expected=expected, actual=actual)
                )
            elif assertion.kind == "field_unchanged":
                after_row = self.observer.snapshot_row(assertion.table, self._key_id(assertion))
                expected = before["row"].get(assertion.field)
                actual = after_row.get(assertion.field)
                results.append(
                    AssertionResult(name=name, status="passed" if actual == expected else "failed", expected=expected, actual=actual)
                )
            elif assertion.kind == "row_created":
                after_count = self.observer.row_count(assertion.table)
                results.append(
                    AssertionResult(name=name, status="passed" if after_count == before["count"] + 1 else "failed", expected=before["count"] + 1, actual=after_count)
                )
            elif assertion.kind == "row_count_unchanged":
                after_count = self.observer.row_count(assertion.table)
                results.append(
                    AssertionResult(name=name, status="passed" if after_count == before["count"] else "failed", expected=before["count"], actual=after_count)
                )
        return results
