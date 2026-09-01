# API 测试 Agent 路线图

## 1. 项目目标

将 API 需求文档转换为可审计、可执行、可复现的接口测试：

```text
OpenAPI / 需求文档
  -> 需求审核
  -> 标准接口用例 JSON
  -> 用例与脚本审核
  -> 接口契约检查
  -> 真实 HTTP 执行
  -> 响应、数据库、日志和业务副作用校验
  -> 机器报告 + 人工报告
```

可信结论不能只依赖 pytest 退出码或 Agent 的自然语言判断，必须能追溯到真实请求、响应和状态证据。

## 2. 当前系统基线

- 被测后端：FastAPI。
- 数据访问：SQLAlchemy ORM。
- 默认数据库：SQLite（`sqlite:///./shop.db`）。
- 现有测试：pytest + requests。
- API 契约：FastAPI 自动生成的 OpenAPI（`/openapi.json`）。
- 人工测试报告：Allure；CI 兼容报告：JUnit XML。

## 3. 共同设计原则

1. Agent 负责理解、规划和审核；确定性工具负责解析、比较、执行、查库和汇总。
2. Agent 之间只传递经过 Pydantic/JSON Schema 校验的 JSON。
3. 每条用例必须有稳定的 `case_id`、`operation_id` 和 `source_refs`。
4. 测试必须区分 `PASS`、`FAIL` 和 `INCONCLUSIVE`。
5. 不能因为实际结果不符合预期，就自动放宽断言。
6. 测试环境与生产环境隔离；Token、密码和数据库连接信息不得进入报告或日志。

## 4. V1：最小可用闭环

### 目标

使用当前 Mini Shop API 验证一条从 OpenAPI 到真实执行的完整链路。

### 范围

- 支持 OpenAPI 3.x JSON；YAML 可在解析器确认后加入。
- 需求审核和接口规范化。
- 生成标准接口用例 JSON。
- 基础覆盖审核：接口、字段、状态码、认证和关键业务规则。
- 根据用例 JSON 生成 pytest 脚本。
- 脚本静态检查：路径、方法、断言、危险调用和用例映射。
- 执行前比较基线 OpenAPI 与目标服务 `/openapi.json`。
- 真实 HTTP 响应校验。
- 通过统一 `DatabaseObserver` 校验库存、余额和订单状态。
- 输出 `execution-report.json`、`coverage-report.json`、`contract-diff.json`、JUnit 和 Allure。

### 必须覆盖的商城场景

- 创建订单后库存减少 `quantity`。
- 创建订单后余额减少订单金额。
- 创建订单后生成正确的订单记录。
- 取消订单后库存和余额恢复。
- 支付、重复支付、取消已支付订单的状态约束。
- 库存不足、余额不足、未认证、资源不存在时没有错误副作用。

### V1 不做

- 不自动修改业务代码。
- 不做复杂自然语言需求理解。
- 不做自动修复闭环。
- 不做并发压测和生产数据库访问。
- 不强制接入 LangChain/LangGraph。

### V1 验收标准

- 每个 OpenAPI operation 都能映射到至少一个用例或明确列入缺口。
- 每个生成脚本都能追溯到 `case_id`。
- 关键业务用例同时检查 HTTP、数据库前后状态，必要时检查日志关联。
- 契约发生破坏性变化时，旧脚本不会直接执行。
- 报告能够回答“哪个需求、哪个用例、哪个请求、什么数据变化导致了结论”。

## 5. V2：审核、日志和受限自修复

### 目标

让 Agent 之间形成稳定的审核回路，减少人工整理和脚本维护成本。

### 范围

- LangChain 用于结构化模型调用；LangGraph 用于有分支的工作流。
- 独立的需求审核、用例覆盖审核、脚本审核和结果审核角色。
- 结构化日志，统一关联 `run_id`、`case_id`、`request_id`、`operation_id`。
- 测试数据隔离、清理和失败现场保留。
- 契约变化后只重新生成受影响的用例和脚本。
- 自动修复次数限制、变更 diff 留档和人工审核节点。
- 失败回滚校验和更完整的 Allure 附件。

### V2 不做

- 不允许 Agent 直接执行任意 shell 或 SQL。
- 不允许自动修改原始需求文档。
- 不自动放行删除、支付、退款等高风险变化。

### V2 验收标准

- 审核失败能回到正确的前置步骤，而不是从头盲目重跑。
- 自动修复最多执行配置的次数，超过后转人工审核。
- 每次修复都能展示修改前后的 JSON 和脚本 diff。
- 日志、HTTP 和数据库证据可以按 `request_id` 关联。

## 6. V3：商城业务真实性和并发

### 目标

验证跨请求、并发和事务行为，而不仅是单请求契约。

### 范围

- 并发下单、库存不超卖、余额不重复扣除。
- 支付、取消和创建订单的幂等性。
- 事务失败时库存、余额和订单记录正确回滚。
- 多用户权限和资源隔离。
- 金额从 `float` 迁移为 `Decimal/Numeric(12,2)`。
- SQLite 测试与 PostgreSQL 测试环境的适配。
- Alembic 数据库迁移和版本记录。

### V3 验收标准

- 并发测试有明确的资源不变量和可重复结果。
- 失败场景不会留下脏数据。
- 金额计算在报告中使用精确的小数语义。
- 数据库 schema 版本和服务版本被记录到执行报告。

## 7. V4：平台化

### 目标

将一次性 CLI 流程升级为可管理的多项目测试平台。

### 范围

- Web 管理界面和任务队列。
- 多项目、多环境、历史运行记录和趋势分析。
- 角色权限、密钥管理和审批流。
- Prompt、模型、Agent 版本管理和 LangSmith 调试。
- 质量门禁、Pull Request 检查和持续集成集成。

## 8. 推荐产物

```text
requirement-review.json
normalized-requirement.json
test-cases.json
coverage-report.json
generated-tests/
script-review.json
contract-diff.json
execution-report.json
junit.xml
allure-results/
evidence/http/
evidence/db/
evidence/logs/
```

`execution-report.json` 是机器事实源；Allure 是人工展示层，不能替代结构化报告和证据文件。

