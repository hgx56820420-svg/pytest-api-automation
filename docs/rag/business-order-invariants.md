---
id: business-order-invariants-001
title: 商城订单必须验证库存余额和状态副作用
type: business_rule
domain: commerce
version: "1.0"
tags: [order, inventory, balance, rollback, state-machine]
applies_to: [testcase_agent, script_review_agent, result_review_agent]
confidence: high
---

## Rule

成功创建订单必须同时产生正确的订单记录、库存扣减和余额扣减。取消未支付订单必须恢复库存和余额，并将状态变为 `cancelled`。支付后状态变为 `paid`，终态订单不能重复支付或取消。

## Required assertions

创建订单前后应验证：

```text
product.stock_after = product.stock_before - quantity
user.balance_after = user.balance_before - order.amount
order.status = created
order.product_id、user_id、quantity、amount 正确
```

取消订单前后应验证库存和余额恢复，以及订单状态变化。

## Failure behavior

库存不足、余额不足、未认证、资源不存在或参数校验失败时，不应扣库存、不应扣余额、不应新增错误订单。

## Counterexamples

只断言 `response.status_code == 201` 不能证明订单业务正确；只看日志中的“创建成功”也不能证明数据库事务已经提交。
