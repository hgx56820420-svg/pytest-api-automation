# 项目概览

## 目标

这是一个用于求职面试展示的 Python + pytest 接口自动化测试项目。重点不是堆测试数量，而是能够解释测试分层、fixture、参数化、契约校验、边界和副作用验证为什么这样设计。

## 当前路线

1. FastAPI 被测服务：已完成
2. 裸写第一个测试：已完成
3. HTTP/fixture/多环境基础：已完成
4. 数据驱动、响应结构和 Faker：进行中
5. Allure 报告与 GitHub Actions：未开始
6. README、架构图和设计决策：未开始

## 目录职责

- `app/`：被测 FastAPI 服务，默认不修改
- `tests/`：学习者编写的接口测试
- `conftest.py`：`base_url`、用户、token、商品等 fixture
- `framework/`：预留的测试框架封装目录，使用前先确认目标
- `STATUS.yaml`：动态当前状态，优先级高于历史交接叙述
- `DECISIONS.md`、`LESSONS.md`：长期可复用知识

## 关键业务契约

- 服务地址：`http://127.0.0.1:8010`
- 订单状态：`created -> paid` 或 `created -> cancelled`，终态不可逆
- 取消订单必须恢复库存和余额
- 访问其他用户订单返回 404，避免泄露资源存在性
- FastAPI 业务错误字段为 `detail`
