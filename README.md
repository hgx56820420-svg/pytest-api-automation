# pytest-api-automation

需求文档驱动的 API 自动化测试 Agent：从 Markdown 需求文档解析接口，经多 Agent 审核生成用例与 pytest 脚本，通过契约门禁后执行真实 HTTP + 数据库校验，产出可审计的机器报告与证据链。

> Agent 负责理解、规划和审核；确定性工具负责解析、比较、执行、查库和汇总。
> 真实结论必须能追溯到请求、响应和状态证据，缺失证据一律 `INCONCLUSIVE`。

## 核心特性

- **需求文档优先**：`REQ-XX-001` 格式的 Markdown 需求文档是主要输入，OpenAPI 退为运行时契约基准
- **多 Agent 工作流**：LangGraph StateGraph 编排 9 个 Agent 节点，Agent 间只传递 Pydantic 校验的消息信封
- **审核路由回路**：审核失败路由回正确的前置 Agent，不盲目重跑全流程
- **受限自修复**：修复次数有上限（默认 2 次），每次修复留 before/after diff，无进展或超限转人工（`NEEDS_HUMAN`）
- **契约门禁**：基线与运行时 OpenAPI 比对，破坏性变化触发受影响用例的选择性再生
- **证据链**：`run_id / case_id / operation_id / request_id` 四方关联，HTTP/数据库 before-after 快照、JSONL 日志、Allure 附件
- **多被测对象**：adapter 插件机制，接入新服务只需注册一个 `DomainAdapter`，框架层零改动

## 架构

```text
需求文档 (MD)                          OpenAPI 契约
     │                                      │
     ▼                                      ▼
┌───────────────────── LangGraph StateGraph ─────────────────────┐
│ 需求解析 → 需求审核 → 用例设计 → 覆盖审核 → 脚本生成 → 脚本审核   │
│     ▲          (失败回退到正确前置步骤，受限修复 ≤2 次)          │
│     │                                                         │
│     └─ contract_gate ──► executor ──► result_reviewer ──► finalize
│              ▲                │            │                   │
│              └─ 选择性再生 ◄───┘      失败进修复回路 / NEEDS_HUMAN  │
└────────────────────────────────────────────────────────────────┘
     │
     ▼
产物：parsed-requirement / test-cases / coverage / contract-diff /
     execution-report / result-review / workflow-report / repair-history /
     evidence/<run_id>/*.json + agent-log.jsonl + Allure + JUnit
```

## 被测对象（adapter）

| Adapter | 服务 | 业务维度 | 接口 / 用例 | 端口 |
|---|---|---|---|---|
| `mini_shop`（默认） | [`app/`](app/) Mini Shop 电商 | 库存/余额副作用、购物车、优惠券、订单状态机 | 22 / 38 | 8010 |
| `library` | [`services/library/`](services/library/) 图书借阅 | 副本/押金副作用、借阅状态机（borrowed→returned） | 12 / 26 | 8020 |
| `meeting` | [`services/meeting/`](services/meeting/) 会议室预约 | 时间窗冲突（重叠 409、相邻放行）、金额快照 | 12 / 28 | 8030 |

三个领域共用同一套框架（工作流、审核、修复、证据链），每个 adapter 只注册四样东西：operation 场景映射、附加用例模板、场景执行器类、数据库观察者类（见 [`api_agent/adapters.py`](api_agent/adapters.py)）。

## 快速开始

```powershell
git clone https://github.com/hgx56820420-svg/pytest-api-automation.git
cd pytest-api-automation
python -m pip install -r requirements.txt
```

运行单测：

```powershell
python -m pytest tests/ -v
```

跑一次完整的 V2 多 Agent 工作流（以 Meeting 为例）：

```powershell
# 终端 1：启动被测服务（独立 SQLite，禁止连生产库）
$env:MEETING_DATABASE_URL = "sqlite:///./meeting-v2.db"
python -m uvicorn services.meeting.main:app --host 127.0.0.1 --port 8030

# 终端 2：运行工作流
python -m api_agent v2 `
  --openapi http://127.0.0.1:8030/openapi.json `
  --requirements-md docs/MEETING_API_REQUIREMENTS.md `
  --base-url http://127.0.0.1:8030 `
  --database-url sqlite:///./meeting-v2.db `
  --output artifacts/meeting-v2 `
  --adapter meeting
```

退出码：`0` PASS；`1` FAIL（业务断言失败）；`3` NEEDS_HUMAN（转人工审核）。

V1 命令（`generate` / `check` / `run` / `pipeline`）保持兼容，见 [`docs/V1_USAGE.md`](docs/V1_USAGE.md)；V2 详细说明见 [`docs/V2_USAGE.md`](docs/V2_USAGE.md)。

Allure 报告：`.\scripts\run_allure.ps1`（需要 Java 21+，详见 [`NEXT_MACHINE.md`](NEXT_MACHINE.md)）。

## 接入新被测对象

1. 新建 `services/<领域>/` FastAPI 服务（含认证 + CRUD + 状态机 + 可断言的副作用）
2. 新增 `DomainAdapter`：operation 场景映射 + 附加用例模板 + `Executor`/`Observer` 子类 + 通配认证规则
3. 编写 `docs/<领域>_API_REQUIREMENTS.md`（`#### REQ-XX-001 \`METHOD /path\`` 标题格式）
4. 以 `--adapter <name>` 运行，框架层不需要任何改动

## 测试与 CI

```text
tests/test_agent_v1.py        V1 规划/契约/脚本审核（9 个用例）
tests/test_agent_v2.py        V2 工作流路由/受限修复/证据关联（12 个用例）
tests/test_agent_library.py   Library adapter 二次验证（8 个用例）
tests/test_agent_meeting.py   Meeting adapter 二次验证（6 个用例）
```

GitHub Actions（[`.github/workflows/tests.yml`](.github/workflows/tests.yml)）在每次 push 时运行全部测试。

## 安全边界

- HTTP 目标仅允许 `127.0.0.1` / `localhost`（hostname 精确白名单）
- 数据库观察者仅接受显式 SQLite 测试库，禁止生产库
- Agent 不执行任意 shell / SQL；删除、支付等高风险断言不自动放宽
- Token、密码、连接串写入证据前递归脱敏；缺证据不报 PASS

## Roadmap

- [x] **V1** 最小可用闭环：OpenAPI → 用例 → pytest → 契约门禁 → 执行报告
- [x] **V2** 多 Agent 审核回路 + 受限修复 + 结构化日志 + 多 adapter
- [ ] **V3** 并发下单防超卖、幂等性、金额 Decimal 化、PostgreSQL + Alembic
- [ ] **V4** Web 平台化：多项目、历史趋势、审批流、Prompt/模型版本管理
