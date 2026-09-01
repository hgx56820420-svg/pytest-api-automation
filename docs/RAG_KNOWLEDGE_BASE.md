# API 测试 Agent RAG 知识库

本文件是知识库索引和通用原则。实际知识条目位于同目录 `rag/`，每个条目都可以独立切分、嵌入和检索。

知识库用于提供可复用规则，不替代当前 OpenAPI、数据库快照或真实执行证据。

## 条目索引

| ID | 文件 | 用途 |
|---|---|---|
| `architecture-agent-pipeline-001` | `rag/architecture-agent-pipeline.md` | 多 Agent 流程和职责边界 |
| `contract-openapi-fastapi-001` | `rag/contract-openapi-fastapi.md` | FastAPI、OpenAPI 和契约校验 |
| `business-order-invariants-001` | `rag/business-order-invariants.md` | 订单库存、余额、状态和回滚断言 |
| `evidence-http-db-log-001` | `rag/evidence-http-db-log.md` | 真实运行证据和数据库观察 |
| `review-coverage-script-001` | `rag/review-coverage-script.md` | 用例覆盖与脚本审核 |
| `contract-drift-repair-001` | `rag/contract-drift-repair.md` | 契约变化、阻断和受限修复 |
| `reporting-trustworthy-results-001` | `rag/reporting-trustworthy-results.md` | JSON、JUnit、Allure 报告分工 |

## 条目格式

每个知识条目保存为一个 Markdown 文件，文件开头使用 YAML front matter：

```yaml
---
id: rule-commerce-order-stock-001
title: 创建订单必须校验库存副作用
type: business_rule
domain: commerce
version: "1.0"
tags: [order, inventory, database, assertion]
applies_to: [testcase_agent, script_review_agent, result_review_agent]
source: project
confidence: high
---
```

正文建议固定为：

```markdown
## Rule

## Why it matters

## Required assertions

## Counterexamples

## Evidence requirements

## Related schemas or cases
```

## 分类

### contract

OpenAPI、参数、响应、状态码、认证和契约 diff 规则。

### business_rule

库存、余额、订单状态、幂等性、事务回滚等业务不变量。

### test_pattern

正常、边界、异常、安全、状态机和并发测试模式。

### review_rule

需求覆盖审核、脚本静态审核、断言充分性和危险代码检查。

### runtime_evidence

HTTP、数据库快照、结构化日志和证据关联规则。

### incident

历史缺陷、失败案例、根因和防回归用例。

### architecture

FastAPI、SQLAlchemy、SQLite/PostgreSQL、pytest、Allure 等项目技术约定。

## 通用原则

1. Agent 负责理解、规划和审核；确定性工具负责解析、比较、执行、查库和汇总。
2. 每个 Agent 的输出必须通过 Pydantic/JSON Schema 校验，并包含来源引用和证据。
3. 真实接口测试必须区分 `PASS`、`FAIL` 和 `INCONCLUSIVE`。
4. 不能因为实际结果不符合预期就自动放宽断言。
5. HTTP 日志不能单独证明数据库事务已经提交；关键业务必须结合数据库前后快照。
6. 测试数据库必须隔离，生产数据库禁止被 Agent 访问。

## 检索和使用规则

- 先按 `domain`、`type`、`tags` 和 `applies_to` 过滤，再做语义检索。
- 业务规则优先于通用测试模板。
- 与当前 OpenAPI 或项目版本不匹配的条目只能作为参考，并标记 warning。
- 每次引用知识条目时，把条目 `id` 写入 Agent 输出的 `evidence` 或 `source_refs`。
- RAG 内容不能覆盖当前运行时契约和真实数据库证据。
- 冲突条目不能静默选择，应输出 `needs_review`。

## 与 Agent 的关系

```text
需求审核 Agent       检索 contract / architecture
用例设计 Agent       检索 business_rule / test_pattern
脚本审核 Agent       检索 review_rule / runtime_evidence
结果审核 Agent       检索 business_rule / incident / runtime_evidence
```

## 建议的存储演进

### V1

Markdown 文件 + YAML metadata + 本地关键词/向量检索。先验证知识条目是否真的改善生成质量。

### V2

将条目切分成稳定 chunks，建立向量索引；保留原文件和条目 ID，确保结果可追溯。

### V3

增加版本、项目、环境和有效期字段；将历史执行报告中的失败模式转成 `incident` 条目，但必须经过审核后入库。

## 不应进入知识库的内容

- Token、密码、数据库连接串等敏感信息。
- 未验证的 Agent 猜测。
- 没有来源的“接口应该返回 200”结论。
- 单次运行的临时 ID 和完整用户数据。
- 未经审核的自动修复结果。
