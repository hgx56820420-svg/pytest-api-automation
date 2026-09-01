---
id: contract-drift-repair-001
title: 契约变化后的受限自修复
type: contract
domain: api-testing
version: "1.0"
tags: [drift, repair, breaking-change, human-review]
applies_to: [contract_diff, testcase_agent, review_agent]
confidence: high
---

## Rule

发现契约变化后，先生成差异报告并定位受影响的 `case_id`，再只重新生成受影响的用例和脚本。自修复不能直接修改原始需求，也不能通过放宽断言掩盖失败。

## Allowed repair

兼容性变化可以记录 warning 后继续。破坏性变化默认阻断；经过规则审核后最多自动修复配置次数，超过次数进入人工确认。

## Required evidence

保存基线和运行时 OpenAPI hash、变化 diff、受影响用例、修复前后 JSON 和脚本 diff。业务规则变化、删除接口、支付/退款/删除等高风险变化默认需要人工确认。
