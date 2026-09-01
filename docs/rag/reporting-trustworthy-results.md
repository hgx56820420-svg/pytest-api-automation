---
id: reporting-trustworthy-results-001
title: API 测试报告的事实源和展示层
type: runtime_evidence
domain: api-testing
version: "1.0"
tags: [report, json, allure, junit, audit]
applies_to: [result_review_agent, reporting]
confidence: high
---

## Rule

`execution-report.json` 是机器可读的唯一事实源；`contract-diff.json` 保存执行前契约变化；`coverage-report.json` 保存需求和脚本覆盖；JUnit XML 用于 CI；Allure 是人工查看的展示层。

## Required decision

最终状态只使用 `PASS`、`FAIL` 和 `INCONCLUSIVE`。报告必须引用 `case_id`、请求/响应证据、数据库快照和日志证据路径，能够回答“为什么通过或失败”。

## Anti-patterns

不能只根据 pytest 退出码、Allure 页面或 Agent 的自然语言摘要判定接口可信。没有关键数据库证据时，业务副作用应为 `INCONCLUSIVE`。
