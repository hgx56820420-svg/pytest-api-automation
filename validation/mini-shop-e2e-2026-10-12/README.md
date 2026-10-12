# Mini Shop E2E Validation Snapshot

这是运行 `run-74225da6cb5e` 的公开验证快照，用于展示框架对仓库自建 Mini Shop adapter 的真实端到端验证结果。

## 结果

- 工作流状态：`PASS`
- 用例总数：38
- 通过：38
- 失败：0
- `INCONCLUSIVE`：0
- JUnit：38 tests，0 failures，0 errors
- 结果审核：`approved`
- 证据：38 份逐用例 JSON，另附 199 条 Agent JSONL 日志记录

## 验证范围

- 测试执行通过本地真实 HTTP 服务完成，目标地址为 `127.0.0.1:8010`。
- 被测服务使用仓库自建的 Mini Shop 和独立 SQLite 数据库；不是 stub，也不是外部生产服务。
- 覆盖认证、商品、购物车、优惠券、订单、支付、库存交易以及负向场景和状态副作用。
- 需求解析、用例设计、覆盖率审核、脚本审核、契约门禁、执行和结果审核均保留为可追溯产物。
- 本次运行的 `analyze_requirements` 节点未启用 LLM，`llm-retry-history.json` 记录了该决定；因此本快照不证明 LLM 分析能力。

## 文件说明

- `workflow-report.json`：工作流阶段、路由和最终决定。
- `execution-report.json`：逐用例执行结果、请求/响应摘要和断言。
- `result-review.json`：结果审核及证据关联结论。
- `junit.xml`：标准 JUnit 汇总。
- `evidence/run-74225da6cb5e/`：逐用例证据和 Agent 阶段日志。
- `generated-tests/test_generated_api.py`：本次生成并执行的 pytest 脚本。

出于安全和可复现性考虑，未上传 SQLite 数据库、LangGraph checkpoint、Allure 原始目录、服务日志、PID、缓存和 Python 缓存。报告中的凭据字段已脱敏为 `[REDACTED]`。

这份快照证明的是该框架在仓库自建 Mini Shop 上的一次本地 E2E 运行，不等同于真实企业项目或生产环境质量结论。
