---
id: evidence-http-db-log-001
title: 真实 API 结果需要 HTTP 数据库和日志三类证据
type: runtime_evidence
domain: api-testing
version: "1.0"
tags: [evidence, http, database, logging, request-id]
applies_to: [runner, result_review_agent]
confidence: high
---

## Rule

可信运行结论应关联真实 HTTP 请求、响应、数据库前后快照和结构化日志。日志用于链路关联和诊断，不能单独证明数据库变化。

## Required fields

每次运行记录 `run_id`、`case_id`、`operation_id` 和 `request_id`。数据库观察器应提供用户、商品和订单快照及 before/after 差异，不让生成脚本自由编写 SQL。

## Decision

HTTP、响应 schema 和关键数据库不变量全部通过时才能判定 `PASS`。证据不足、无法查测试数据库或服务未启动时判定 `INCONCLUSIVE`，不能伪装成通过。

## Security

DatabaseObserver 只允许测试数据库和只读访问；Token、密码和数据库连接串不得进入日志、Allure 附件或普通报告。
