---
id: contract-openapi-fastapi-001
title: FastAPI OpenAPI 是 API 测试的机器契约
type: contract
domain: api-testing
version: "1.0"
tags: [fastapi, openapi, schema, contract]
applies_to: [requirement_agent, contract_diff, script_review_agent]
confidence: high
---

## Rule

OpenAPI 描述路径、HTTP 方法、参数、请求体、响应 schema、状态码和认证要求。FastAPI 会根据路由和 Pydantic 模型自动生成 `/openapi.json`，因此需求审核 Agent 应把 OpenAPI 解析成统一接口模型，而不是依赖自然语言猜测字段。

## Required assertions

执行前比较基线 OpenAPI 与目标服务当前 `/openapi.json`。至少比较路径、方法、参数类型和必填性、请求体、响应字段、状态码和认证要求。

## Decision

新增可选字段通常是 `warning`；删除接口、删除必填字段、新增必填请求字段、类型改变或认证要求改变是 `breaking`，应阻断旧脚本执行。

## Limitation

OpenAPI 不能完整表达“下单后库存减少”这类业务不变量，需要额外业务规则和数据库证据。
