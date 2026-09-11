"""Requirement Analyst Agent: LangChain structured extraction of test rules.

输入：需求文档全文 + 契约接口清单 + 可观察表白名单。
输出：LLMRuleSet（声明式规则），经 with_structured_output 强制通过
Pydantic 校验。LLM 只做"理解与拆解"，所有断言由确定性 DSL 引擎执行。
"""

from __future__ import annotations

import json

from api_agent.llm_client import get_chat_model, llm_setting
from api_agent.llm_rules import LLMRuleSet
from api_agent.models import NormalizedRequirement

MAX_DOC_CHARS = 24000

SYSTEM_PROMPT = """你是资深接口测试需求分析师。把需求文档拆解成可执行的业务测试规则。

硬性约束：
1. action_method + action_path 必须来自提供的接口清单，禁止编造接口。
2. setup 只允许两个原语：register（注册并登录测试用户，产出 user）、
   create（调用清单中的创建类接口造前置数据，产出 resource 指定名的资源）。
3. db_assertions 的 table 必须来自提供的可观察表白名单，禁止编造表名/列名。
4. 断言是声明式 DSL：field_delta（字段变化量）、field_unchanged（字段不变）、
   row_created（表新增一行）、row_count_unchanged（表行数不变）。
   禁止写 SQL，禁止写代码。
5. 请求体里引用前置资源用 {{资源名.字段}} 占位符，如 {{product.id}}；
   其余为字面量，必须满足该接口的参数约束（数值范围、字符串长度）。
6. 每条规则给出需求文档原文引用（source_quote）供人工审计。
7. 只输出有明确业务规则的用例（成功路径的资金/状态副作用、失败路径的
   无副作用不变量），不要为每个接口凑数。"""


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


def analyze_requirements(
    requirement: NormalizedRequirement,
    requirements_md,
    observable_tables: dict[str, str],
) -> LLMRuleSet:
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
    result = structured.invoke(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        config={"configurable": {}},
    )
    if result is None:
        # 兜底：部分网关/模型不回工具调用，改走纯文本 JSON 再人工校验
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
    return result


def analyst_model_name() -> str:
    return llm_setting("LLM_MODEL")
