"""Pydantic contracts for the LLM requirement analyst.

LLM 的输出被 with_structured_output 强约束为 :class:`LLMRuleSet`。
规则是**声明式**的：setup/action 只允许三个确定性原语，断言只允许
四种 DSL，由确定性引擎执行——LLM 永远不产生 SQL 或代码。

字段命名以模型的真实输出习惯为准（action/action_params/operator），
未知字段宽容忽略；真正的安全边界是 adapter 的可观察表白名单。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, ConfigDict, Field

from api_agent.models import StrictModel

SetupAction = Literal["register", "create"]
AssertionKind = Literal["field_delta", "field_unchanged", "row_created", "row_count_unchanged"]


class LLMSetupStep(StrictModel):
    """One deterministic pre-step the executor knows how to perform."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    action: SetupAction = "register"
    resource: str = Field(default="", description="产出资源名，如 product；register 固定产出 user")
    method: Literal["POST", "PUT", "PATCH"] = "POST"
    path: str = Field(default="", description="create 原语的接口路径")
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="覆盖 schema 默认构造值，如 {\"stock\": 5, \"price\": 10.0}",
    )


class LLMDBAssertion(StrictModel):
    """One declarative database assertion, executed by the DSL engine."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    kind: AssertionKind = Field(validation_alias=AliasChoices("kind", "operator", "assertion"))
    table: str = Field(default="", description="目标表名，必须在 adapter 观察白名单内")
    field: str = Field(default="", description="field_delta/field_unchanged 的目标列")
    key_resource: str = Field(
        default="",
        validation_alias=AliasChoices("key_resource", "key", "key_by_resource", "resource"),
        description="行定位资源（setup/action 产出的资源名），按其 id 查行",
    )
    delta: float | None = Field(default=None, validation_alias=AliasChoices("delta", "value", "change"), description="field_delta 的期望变化量")


class LLMRule(StrictModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    rule_id: str = Field(description="全局唯一，建议 REQ-XXX-NNN 场景缩写")
    title: str = ""
    action: str = Field(default="", description='被测请求，如 "POST /api/orders"，可含 {{resource.field}} 占位符')
    action_params: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("action_params", "action_body", "params", "body"),
        description="请求体，值可引用 {{resource.field}} 占位符，其余为字面量",
    )
    action_headers: dict[str, Any] = Field(
        default_factory=dict,
        description="模型可能自作主张输出的请求头；执行器统一使用自己的认证，此字段仅接受不使用",
    )
    expected_status_codes: list[int] = Field(default_factory=list, validation_alias=AliasChoices("expected_status_codes", "expected_status", "status_codes"))
    setup: list[LLMSetupStep] = Field(default_factory=list)
    db_assertions: list[LLMDBAssertion] = Field(default_factory=list)
    source_quote: str = Field(default="", description="需求文档原文引用，供人工审计")


class LLMRuleSet(StrictModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    rules: list[LLMRule] = Field(default_factory=list)
