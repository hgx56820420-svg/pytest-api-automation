---
id: review-coverage-script-001
title: 用例和脚本审核必须检查业务断言覆盖
type: review_rule
domain: api-testing
version: "1.0"
tags: [coverage, review, script, assertions]
applies_to: [coverage_review_agent, script_review_agent]
confidence: high
---

## Rule

审核不能只统计测试函数数量。必须建立需求 operation、schema 字段、错误状态码、认证要求、业务规则和生成脚本之间的覆盖矩阵。

## Required checks

- 每个接口至少有一个用例，缺口必须显式报告。
- 必填字段、边界值和错误输入有对应场景。
- 未认证、无效认证、资源不存在和非法状态流转被覆盖。
- 创建订单、取消订单和失败回滚包含库存、余额、订单状态或数据库断言。
- 每个 `case_id` 都映射到一个生成测试；测试不能只断言状态码。
- 脚本不能使用任意 shell、任意 SQL、吞掉异常或硬编码敏感信息。

## Decision

缺少关键业务断言时输出 `needs_revision`，即使当前脚本全部返回 200 也不能批准。
