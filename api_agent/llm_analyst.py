"""Requirement Analyst Agent: LangChain structured extraction of test rules.

输入：需求文档全文 + 契约接口清单 + 可观察表白名单。
输出：LLMRuleSet（声明式规则），经 with_structured_output 强制通过
Pydantic 校验。LLM 只做"理解与拆解"，所有断言由确定性 DSL 引擎执行。
"""

from __future__ import annotations

import json
import time

from api_agent.llm_client import get_chat_model, llm_setting
from api_agent.llm_rules import LLMRuleSet
from api_agent.models import NormalizedRequirement

MAX_DOC_CHARS = 24000

SYSTEM_PROMPT = """你是资深接口测试需求分析师。把需求文档拆解成可执行的业务测试规则。

输出规则字段（严格遵守，多余字段会被丢弃）：
- rule_id: 全局唯一，如 REQ-ORDER-001.stock
- title: 场景名
- action: "METHOD 路径"，如 "POST /api/orders"，路径可含 {{资源.字段}} 占位符
- action_params: 请求体对象；引用前置资源用 {{资源.字段}} 占位符（如 {{product.id}}），
  其余为字面量且必须满足接口参数约束（数值范围、字符串长度）
- setup: 前置步骤数组，只允许两种原语：
    {"action": "register", "resource": "user"}
    {"action": "create", "resource": "product", "path": "/api/products", "overrides": {"stock": 5, "price": 10.0}}
- db_assertions: 数据库断言数组，table 必须来自提供的可观察表白名单，四种原语：
    {"kind": "field_delta", "table": "products", "field": "stock", "key_resource": "product", "delta": -2}
    {"kind": "field_unchanged", "table": "users", "field": "balance", "key_resource": "user"}
    {"kind": "row_created", "table": "orders"}
    {"kind": "row_count_unchanged", "table": "users"}
- expected_status_codes: 期望状态码数组
- source_quote: 需求文档原文引用，供人工审计

其他硬性约束：
1. action 的 METHOD 和路径必须来自提供的接口清单，禁止编造接口。
2. 禁止写 SQL，禁止写代码；断言只有上面四种 DSL。
3. 【最重要】action_params 里凡引用 setup 产出资源的字段，必须用
   {{资源.字段}} 占位符（如 "product_id": "{{product.id}}"）；**绝对禁止**
   写死数据库自增 ID（如 "product_id": 1）——自增 ID 每次运行都不同，
   写死必然打错资源。数值型非 ID 字段（如 quantity: 2）才是合法字面量。
4. field_delta 的 delta 必须与 setup.overrides 中的数值**自洽**：
   例如 setup 造的产品 price=50、action quantity=2，则余额断言
   delta 应为 -100（=-50×2），禁止照抄文档示例里的其他数字。
5. 只输出有明确业务规则的用例（成功路径的资金/状态副作用、失败路径的
   无副作用不变量），不要为每个接口凑数。
6. 同一用例内需要多个测试账号时不要重复注册同名用户；一般一个
   register 足够，跨用户隔离场景才注册第二个。"""


def _interface_catalog(requirement: NormalizedRequirement) -> str:
    lines = []
    for operation in requirement.operations:
        request_desc = ""
        if operation.request_schema:
            request_desc = " 请求体: " + json.dumps(
                {
                    name: {
                        key: value
                        for key, value in prop.items()
                        if key in {"type", "minimum", "maximum", "exclusiveMinimum", "minLength", "maxLength", "enum", "pattern"}
                    }
                    for name, prop in (operation.request_schema.get("properties") or {}).items()
                },
                ensure_ascii=False,
            )
        lines.append(f"- {operation.method} {operation.path}{request_desc}")
    return "\n".join(lines)


def repair_resource_ids(rules: LLMRuleSet) -> list[str]:
    """确定性护栏：修复 LLM 常见错误——把写死的 <资源>_id 数字字段替换为占位符。

    模型经常无视"禁止写死 ID"的指令（如 "product_id": 1）。只要 setup 里
    存在同名资源，就把该字段重写为 {{资源.id}}；返回修复记录供审计。
    无同名资源时保持原样，交由数据库断言如实暴露。
    """
    repairs: list[str] = []
    for rule in rules.rules:
        resource_names = {step.resource for step in rule.setup if step.resource} | (
            {"user"} if any(step.action == "register" for step in rule.setup) else set()
        )
        for key, value in list(rule.action_params.items()):
            if isinstance(value, int) and not isinstance(value, bool) and key.endswith("_id"):
                resource_name = key[: -len("_id")]
                if resource_name in resource_names:
                    rule.action_params[key] = "{{" + resource_name + ".id}}"
                    repairs.append(f"{rule.rule_id}: {key} -> {{{{{resource_name}.id}}}}")
    return repairs


def analyze_requirements(
    requirement: NormalizedRequirement,
    requirements_md,
    observable_tables: dict[str, str],
) -> tuple[LLMRuleSet, list[str]]:
    """Call the LLM once and return validated rules (or raise)."""
    text = requirements_md.read_text(encoding="utf-8-sig")
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS] + "\n...(文档已截断)"

    catalog = _interface_catalog(requirement)
    tables = ", ".join(sorted(observable_tables)) or "(无)"
    user_prompt = (
        f"# 需求文档\n{text}\n\n"
        f"# 契约接口清单（action_path 只能用这些）\n{catalog}\n\n"
        f"# 可观察表白名单（db_assertions.table 只能用这些）\n{tables}\n"
    )

    model = get_chat_model()
    # 网关兼容性：function_calling 而不是 json_schema（OpenAI 专属强约束）
    structured = model.with_structured_output(LLMRuleSet, method="function_calling")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    result = None
    last_error: Exception | None = None
    for attempt in range(1, 5):  # 免费档高峰期常见 429，耐心跨过限流窗口
        try:
            result = structured.invoke(messages)
            if result is not None:
                break
        except Exception as exc:  # noqa: BLE001 - 记录最后一次错误后统一退避重试
            last_error = exc
        time.sleep(20 * attempt)
    if result is None:
        # 兜底 1：部分网关/模型不回工具调用，改走纯文本 JSON 再人工校验
        for attempt in range(1, 4):
            try:
                raw = model.invoke(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT + "\n只输出一个 JSON 对象，不要任何其他文字。"},
                        {"role": "user", "content": user_prompt},
                    ]
                )
                text = str(raw.content)
                start, end = text.find("{"), text.rfind("}")
                if start == -1 or end <= start:
                    raise RuntimeError(f"LLM returned no structured output; raw text: {text[:200]}")
                result = LLMRuleSet.model_validate_json(text[start : end + 1])
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(20 * attempt)
    if result is None:
        raise RuntimeError(f"LLM analysis failed after retries: {last_error}")
    repairs = repair_resource_ids(result)
    return result, repairs


def analyst_model_name() -> str:
    return llm_setting("LLM_MODEL")
