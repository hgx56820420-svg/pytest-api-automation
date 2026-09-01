---
id: architecture-agent-pipeline-001
title: API 测试 Agent 多阶段流水线
type: architecture
domain: api-testing
version: "1.0"
tags: [agent, workflow, testcase, pytest]
applies_to: [requirement_agent, testcase_agent, script_agent, review_agent]
confidence: high
---

## Rule

API 测试 Agent 应采用“需求审核 -> 用例 JSON -> 覆盖审核 -> 脚本生成 -> 脚本审核 -> 契约检查 -> 真实执行 -> 结果审核”的流水线。Agent 负责语义工作，OpenAPI parser、JSON Schema validator、HTTP client、DatabaseObserver、日志采集器和 pytest runner 负责确定性工作。

## Required output

每条用例必须包含 `case_id`、`operation_id`、`request`、`expected`、`business_assertions` 和 `source_refs`。脚本只能消费已审核的用例 JSON，不应重新解释原始需求。

## Evidence requirements

需求、用例、脚本和运行结果必须能够通过 `case_id` 互相追溯。审核失败时应回到对应阶段，不应让后续 Agent 猜测缺失信息。

## Anti-patterns

不要让一个 Agent 同时自由解析文档、生成任意 Python、执行任意 shell 并自行宣布通过。
