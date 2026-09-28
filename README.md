# pytest-api-automation

需求文档驱动的 API 自动化测试 Agent：以 Markdown 需求为业务事实来源，以 OpenAPI 为可执行契约基线，经多 Agent 审核生成用例与 pytest 脚本，通过契约门禁后执行真实 HTTP + SQLite 校验，产出可审计的机器报告与证据链。

> Agent 负责理解、规划和审核；确定性工具负责解析、比较、执行、查库和汇总。
> 真实结论必须能追溯到请求、响应和状态证据，缺失证据一律 `INCONCLUSIVE`。

## 核心特性

- **需求文档优先**：`REQ-XX-001` 格式的 Markdown 需求文档是主要输入，OpenAPI 退为运行时契约基准
- **多 Agent 工作流**：LangGraph StateGraph 编排 12 个工作流节点，Agent 间只传递 Pydantic 校验的消息信封和产物引用
- **审核路由回路**：审核失败路由回正确的前置 Agent，不盲目重跑全流程
- **受限自修复**：修复次数有上限（默认 2 次），每次修复留 before/after diff，无进展或超限转人工（`NEEDS_HUMAN`）
- **契约门禁**：基线与运行时 OpenAPI 比对，破坏性变化触发受影响用例的选择性再生
- **证据链**：`run_id / case_id / operation_id / request_id` 四方关联，HTTP/数据库 before-after 快照、JSONL 日志、Allure 附件
- **多被测对象**：adapter 插件机制，接入新服务只需注册一个 `DomainAdapter`，框架层零改动
- **LangGraph 内核拆分**：独立图定义、checkpoint 状态与 reducer、模型重试/降级子图；Python API 支持断点恢复，详见 [内核说明](docs/LANGGRAPH_KERNEL.md)

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

三个领域共用同一套框架（工作流、审核、修复、证据链）。每个 adapter 注册 operation 场景映射、附加用例、executor/observer 类、章节级认证规则、清理规格和可观察表白名单（见 [`api_agent/adapters.py`](api_agent/adapters.py)）。

## 设计与代码结构

项目把不确定的“理解”与可验证的“执行”分开：Agent/LLM 负责理解、拆解和审核；确定性 Python 工具负责解析、OpenAPI 比较、用例编译、pytest 生成、HTTP 调用、数据库查询和结论汇总。LLM 不能生成或执行 Python、Shell、SQL，也不能扩大 adapter 的数据库表白名单。

| 层 | 主要模块 | 职责 |
|---|---|---|
| 输入与契约 | `requirement_parser.py`、`requirements.py`、`openapi.py`、`contract.py` | 解析 Markdown，规范化 OpenAPI，审核双向映射并检测运行时漂移 |
| 图与状态 | `workflow_graph.py`、`graph_state.py`、`workflow.py` | 定义主图拓扑、条件路由、checkpoint state、reducer、修复预算和恢复入口 |
| LLM 子图 | `analyst_graph.py`、`llm_analyst.py`、`llm_rules.py`、`llm_client.py` | 结构化规则提取、图原生重试、JSON 降级、Pydantic 校验和 ID 护栏 |
| 规划与生成 | `planner.py`、`generator.py` | 生成标准用例 JSON、审核覆盖、输出固定 pytest 模板并做 AST 静态检查 |
| 领域执行 | `adapters.py`、`executor.py`、`library_executor.py`、`meeting_executor.py` | 将 operation 映射到场景，实现 setup、真实请求、响应断言和副作用验证 |
| 数据库断言 | `database.py`、`db_assert.py`、领域 observer | 只读白名单表，采集 before/after 状态并执行声明式断言 |
| 审计报告 | `agentlog.py`、`reporting.py`、`result_review.py`、`artifacts.py` | 记录关联日志，从证据重建执行报告并复核 PASS 的可信度 |
| 被测服务 | `app/`、`services/library/`、`services/meeting/` | 三个独立 FastAPI + SQLite 示例领域 |

`api_agent/pipeline.py` 是 V1 的确定性流水线，也是 V2 执行节点复用的底层入口；`api_agent/cli.py` 提供 `python -m api_agent` 的所有命令和退出码。业务产物通过文件引用传递，因此可以脱离进程逐项审计；LangGraph checkpoint 只保存调度状态和小型规则数据。

## 需求文档处理流程

### 文档格式

每个接口必须使用四级标题，需求 ID 支持多段前缀：

```markdown
#### REQ-ORDER-001 `POST /api/orders` 创建订单

- 认证：需要 Bearer Token
- 成功响应 `201`
- 库存和用户余额必须原子性减少
```

解析器接受 `GET`、`POST`、`PUT`、`PATCH`、`DELETE`、`HEAD`、`OPTIONS`。每个需求 ID 必须唯一，method/path 必须与 OpenAPI 完全对应。adapter 还可以提供章节级认证规则，例如“所有订单接口均需要认证”，解析器会按路径前缀回填接口认证要求。

### 确定性解析与审核

1. `parse_requirements()` 读取 UTF-8/UTF-8 BOM Markdown，提取标题、内容 hash、接口 ID、method/path、原文行号、认证要求和“成功响应”状态码；未声明成功码时默认为 200。
2. 明确的文本 marker 用于检测库存/余额变化、取消恢复、失败无副作用、状态机、跨用户隔离、事务原子性及各类负向场景。缺少 marker 记为 warning；重复 ID 或完全没有合法接口标题记为 issue。
3. 同一节点从 URL 或文件加载 OpenAPI，规范化 operation ID、method/path、参数约束、请求/响应 schema 和安全要求，写入 `normalized-requirement.json`。
4. `review_markdown_requirements()` 做双向审核：Markdown 的每个接口必须存在于 OpenAPI，每个 OpenAPI operation 也必须恰好映射一个需求条目。未知接口、重复映射或漏写 operation 都会阻断。
5. planner 组合契约、需求映射和 adapter 的场景/附加用例，生成带 `case_id`、`operation_id`、来源引用、期望状态码、必需断言和证据类型的 `test-cases.json`。
6. 覆盖审核确认每个 operation 和需求都有用例、scenario 在 executor 中存在、关键业务路径声明了 HTTP/数据库证据。新 operation 即使没有领域映射，也会获得通用契约用例，不会被静默忽略。

需求审核失败回解析节点，覆盖失败回用例设计节点；如果修复后的产物签名不变，工作流立即判定“无进展”并转人工，而不是耗尽所有重试。

## LangGraph 工作流

### 主图拓扑

V2 使用 `StateGraph(V2State)`，真实拓扑包含 12 个节点：

| 节点 | 动作 | 后续路由 |
|---|---|---|
| `parse_requirement` | 解析 Markdown 并建立 OpenAPI 基线 | `review_requirement` |
| `review_requirement` | 审核需求与契约双向映射 | 通过到 `analyze_requirements`；否则回解析或转人工 |
| `analyze_requirements` | 可选 LLM 分析；未显式启用时跳过 | `design_cases`；模型失败也记录 issue 后继续确定性覆盖 |
| `design_cases` | 生成 adapter 用例，合并当前 LLM DSL 用例 | `review_coverage` |
| `review_coverage` | 审核运行场景、operation、来源和断言覆盖 | 通过到 `generate_script`；否则回用例设计或转人工 |
| `generate_script` | 生成受控 pytest 适配入口 | `review_script` |
| `review_script` | AST 安全检查和 case 映射检查 | 通过到 `contract_gate`；否则回脚本生成或转人工 |
| `contract_gate` | 比较基线与运行时 OpenAPI | 兼容则执行；breaking 则选择性再生或转人工 |
| `regenerate_affected` | 仅重建发生 breaking drift 的 operation 用例 | 再次进入 `contract_gate` |
| `execute` | 子进程运行生成的 pytest 并收集真实证据 | `review_results` |
| `review_results` | 审核证据、断言和 request ID 关联 | 通过则收尾；否则有界重跑执行或转人工 |
| `finalize` | 汇总轨迹、产物、修复历史和最终决定 | `END` |

契约门禁比较路由、认证、参数、请求 schema、响应状态码和响应 schema。新增 operation/响应码记 warning；operation 删除、路由/安全/参数/schema 变化和响应删除是 breaking。发生 breaking 时会保留未受影响用例，只重建受影响部分；同时设置人工升级标记，避免服务端误删接口被自动吸收成新基线。

### State、reducer 与 Agent 消息

`V2State` 保存 `run_id`、output 目录、配置摘要、路由、步骤、消息、问题、修复历史、无进展签名、人工升级状态、最终决定和当前 LLM 规则。`steps`、`messages`、`issues`、`repair_history` 使用 `Annotated[list, operator.add]` reducer；节点只返回新增 delta，由 LangGraph 合并，避免恢复或并行 state 合并时覆盖历史。

Agent 间消息是 Pydantic 校验的 `AgentMessage`，包含 run ID、from/to、topic、产物路径、产物 hash、决定和问题。它是业务信封而非聊天记录，所以没有使用 `add_messages`。

### 修复预算与决定

自动修复默认最多 2 次。每次记录 trigger、target、before/after、unified diff 和受影响 case；人工升级本身留在账本中，但不消耗自动修复次数。最终规则如下：

- 结果审核通过且没有人工升级：`PASS`；
- 有可信证据确认失败 case：`FAIL`；
- 修复无进展、预算耗尽、breaking 契约待确认或无法形成可信结论：`NEEDS_HUMAN`。

## LLM 需求分析与声明式 DSL

LLM 默认关闭。仅当命令传入 `--llm-analysis`，且 `.env` 或进程环境同时提供以下配置时启用：

```dotenv
LLM_API_KEY=replace-me
LLM_BASE_URL=https://your-openai-compatible-gateway.example
LLM_MODEL=your-model
```

网关地址已有 `/v2`、`/v4` 等版本段时保持不变，否则补 `/v1`。模型温度为 0、调用超时 180 秒，SDK 的 `max_retries` 为 0，唯一重试预算由 LangGraph 管理。

### Analyst 子图

```text
START → structured extraction ─成功→ repair_ids → END
                  │
                  └─重试耗尽/不支持 tool mode→ text_json → repair_ids
```

- 结构化节点使用 `with_structured_output(LLMRuleSet, function_calling)`，最多 4 次图原生重试。
- 文本 JSON 降级节点最多 3 次，截取 JSON 对象后仍用同一 Pydantic schema 校验。
- 网络错误、超时、429/5xx、无输出、解析/校验失败可重试；认证、权限和编程错误直接抛出。
- 默认指数退避从 2 秒开始，上限 20 秒并带 jitter。
- LLM 整体失败不会阻断确定性用例覆盖，只记录 issue 后继续。
- 当前规则保存在 checkpoint；执行前总会覆盖 `llm-rules.json`，不会误用旧 output 中的遗留规则。

### DSL 结构与示例

LLM 只能输出 setup、被测 action、状态码和四种数据库断言：

```json
{
  "rule_id": "REQ-ORDER-001.stock",
  "title": "下单后库存减少",
  "setup": [
    {"action": "register", "resource": "user"},
    {
      "action": "create",
      "resource": "product",
      "method": "POST",
      "path": "/api/products",
      "overrides": {"stock": 5, "price": 10.0}
    }
  ],
  "action": "POST /api/orders",
  "action_params": {"product_id": "{{product.id}}", "quantity": 2},
  "expected_status_codes": [201],
  "db_assertions": [
    {
      "kind": "field_delta",
      "table": "products",
      "field": "stock",
      "key_resource": "product",
      "delta": -2
    },
    {"kind": "row_created", "table": "orders"}
  ],
  "source_quote": "库存应按下单数量减少"
}
```

setup 原语只有两个：`register` 通过真实认证接口创建测试用户并获得 token；`create` 先按 OpenAPI schema 确定性构造合法 body，再应用有限 overrides，通过真实接口创建资源。

| 数据库断言 | 确定性判定 |
|---|---|
| `field_delta` | `after == round(before + delta, 2)` |
| `field_unchanged` | 目标字段前后不变 |
| `row_created` | 表行数增加 1 |
| `row_count_unchanged` | 表行数前后不变 |

`{{resource.field}}` 在运行时从真实 setup 响应解析。若模型把 `product_id` 等字段写成整数，而 setup 已创建同名资源，ID 护栏会改写成 `{{product.id}}` 并记录修复；无法解析的真实 ID 不会被猜测，断言会如实输出 `inconclusive`。

DSL 的硬边界是：action method/path 必须来自规范化 OpenAPI；表名必须属于当前 adapter 的 `observable_tables`；模型不能提供 SQL；认证由 executor 管理；未知字段不能扩大执行能力。需求文档最多向模型提供前 24,000 字符，超出部分会标记截断。

## 测试生成、执行与证据链

框架不让模型编写任意测试代码。`generate_pytest()` 输出固定模板：读取 `test-cases.json`，按 case 参数化，通过 adapter factory 创建 executor，执行场景并把证据作为 Allure JSON 附件。

脚本审核使用 Python AST，拒绝 `subprocess`、`socket`、`eval`、`exec`、`compile`、`os.system` 和 `os.popen`，并验证脚本使用 adapter factory、pytest 参数化且映射全部 case ID。

通过覆盖、脚本和契约门禁后，pipeline 以子进程运行生成的 pytest，通过环境变量传递本次 base URL、SQLite URL、证据目录、run ID、用例/契约路径、adapter、固定账号开关和规则路径。pytest 超时为 180 秒；超时会保留已有日志/证据并降级为 `INCONCLUSIVE`。

每个请求携带唯一 `X-Request-ID`。密码、token、Authorization、secret 和连接串写盘前递归脱敏。`execution-report.json` 从磁盘证据重建，而不是只相信 pytest 退出码：缺少证据的 case 会补为 `inconclusive`。结果审核再次验证：

- 每个 case 的证据文件存在；
- evidence 包含 request ID；
- 同一 case/request ID 出现在 `agent-log.jsonl`；
- 没有 failed assertion；
- 报告声称 PASS 时不存在任何审计缺口。

只有这些检查全部通过，PASS 才是可信结论。

## 快速开始

要求 Python 3.13+。依赖在 `requirements.txt` 中精确锁定；当前版本也兼容 Python 3.14。

```powershell
git clone https://github.com/hgx56820420-svg/pytest-api-automation.git
cd pytest-api-automation
python -m venv .venv
.\.venv\Scripts\Activate.ps1
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

可选参数：`--runtime-openapi` 指定实时契约（默认 `<base-url>/openapi.json`）；`--max-repair-attempts` 调整修复预算；`--fixed-accounts` 使用按 case 确定的测试用户名；`--llm-analysis` 启用前述 LLM 子图。

V1 命令（`generate` / `check` / `run` / `pipeline`）保持兼容，见 [`docs/V1_USAGE.md`](docs/V1_USAGE.md)；V2 详细说明见 [`docs/V2_USAGE.md`](docs/V2_USAGE.md)。

Allure 报告：`.\scripts\run_allure.ps1`（需要 Java 21+，详见 [`NEXT_MACHINE.md`](NEXT_MACHINE.md)）。

## V1 分阶段运行

V1 适合单独调试确定性工具或兼容旧流水线：

```powershell
python -m api_agent generate --openapi http://127.0.0.1:8010/openapi.json --requirements-md docs/MINI_SHOP_API_REQUIREMENTS.md --output artifacts/v1
python -m api_agent check --runtime-openapi http://127.0.0.1:8010/openapi.json --output artifacts/v1
python -m api_agent run --base-url http://127.0.0.1:8010 --database-url sqlite:///./agent-v1.db --output artifacts/v1
python -m api_agent pipeline --openapi http://127.0.0.1:8010/openapi.json --requirements-md docs/MINI_SHOP_API_REQUIREMENTS.md --base-url http://127.0.0.1:8010 --database-url sqlite:///./agent-v1.db --output artifacts/v1
```

四个命令依次对应“生成”“契约检查”“执行”和“一次完成全流程”；详细参数见 `docs/V1_USAGE.md` 与 `docs/V2_USAGE.md`。

## 接入新被测对象

1. 新建 `services/<领域>/` FastAPI 服务（含认证 + CRUD + 状态机 + 可断言的副作用）
2. 新增 `DomainAdapter`：operation 场景映射 + 附加用例模板 + `Executor`/`Observer` 子类 + 通配认证规则
3. 编写 `docs/<领域>_API_REQUIREMENTS.md`（`#### REQ-XX-001 \`METHOD /path\`` 标题格式）
4. 以 `--adapter <name>` 运行，框架层不需要任何改动

## Checkpoint、断点与恢复

CLI 默认使用进程内 `InMemorySaver`。Python API 可以传入 checkpointer，并在真实执行前设置静态断点：

```python
from pathlib import Path
from langgraph.checkpoint.memory import InMemorySaver
from api_agent.workflow import V2Workflow

saver = InMemorySaver()
settings = dict(
    output_dir=Path("artifacts/reviewed-run"),
    openapi_source="http://127.0.0.1:8010/openapi.json",
    requirements_md=Path("docs/MINI_SHOP_API_REQUIREMENTS.md"),
    base_url="http://127.0.0.1:8010",
    database_url="sqlite:///./agent-v2.db",
    run_id="reviewed-run-001",
    checkpointer=saver,
)

workflow = V2Workflow(**settings, interrupt_before=["execute"])
workflow.invoke()
snapshot = workflow.graph.get_state(
    {"configurable": {"thread_id": settings["run_id"]}}
)
assert snapshot.next == ("execute",)

# 审核 output 后，用同一个 saver、run_id、目录和配置恢复
resumed = V2Workflow(**settings)
final_state = resumed.resume()
```

- `thread_id` 必须等于 `run_id`，配置摘要必须一致；已有 checkpoint 时再次 `invoke()` 会拒绝并提示使用 `resume()`。
- `InMemorySaver` 不能跨进程；跨进程恢复需调用方安装和管理 SQLite/Postgres saver。
- 图状态保存已完成节点、修复预算、无进展签名和 LLM 规则；大型产物仍在 output 目录，暂停期间不能删除或覆盖。
- checkpoint 是节点级恢复，不回滚 HTTP/数据库副作用。不要为整个 `execute` 节点添加自动 RetryPolicy；恢复前应核对外部状态。
- 并发运行必须使用不同 run ID 和 output 目录。

## 运行产物

| 产物 | 内容 |
|---|---|
| `parsed-requirement.json` | Markdown 确定性解析结果 |
| `normalized-requirement.json` | 规范化 OpenAPI 基线 |
| `requirement-review.json` | 需求与 operation 映射及审核问题 |
| `llm-rules.json` | 当前运行的可选 DSL；未启用时为空规则集 |
| `test-cases.json` | 标准用例文档 |
| `coverage-report.json` | operation、需求、场景和断言覆盖 |
| `generated-tests/test_generated_api.py` | 受控 pytest 入口 |
| `script-review.json` | AST 审核和 case 映射 |
| `contract-diff.json` | 基线与运行时 OpenAPI 差异 |
| `execution-report.json` | 从每用例证据重建的机器事实 |
| `result-review.json` | 证据、request ID 和可信结论审核 |
| `workflow-report.json` | 步骤、消息、问题、产物索引和最终决定 |
| `repair-history.json` | 修复与人工升级、before/after 和 diff |
| `failure-scene.json` | 失败/不确定运行现场索引（按需生成） |
| `evidence/<run_id>/*.json` | 每用例 HTTP、断言和数据库证据 |
| `evidence/<run_id>/agent-log.jsonl` | 可按四个关联 ID 检索的事件日志 |
| `junit.xml`、`allure-results/` | 标准测试结果 |
| `pytest.stdout.log`、`pytest.stderr.log` | 生成测试子进程输出 |

`repair-history.json` 是 checkpoint 的审计投影，恢复时不会反向读取它计算预算。

## 测试与 CI

| 文件 | 重点 |
|---|---|
| `tests/test_agent_v1.py` | OpenAPI 覆盖、需求映射、契约差异、生成脚本和脱敏 |
| `tests/test_agent_v2.py` | 文档解析、结果审核、修复上限、清理保护、DSL 和 V2 路由 |
| `tests/test_agent_library.py` | Library adapter、数据库保护、需求覆盖和端到端执行 |
| `tests/test_agent_meeting.py` | Meeting adapter、时间冲突维度和端到端执行 |
| `tests/test_analyst_graph.py` | 结构化输出、图重试、JSON 降级、认证错误和 SDK 配置 |
| `tests/test_langgraph_kernel.py` | pause/resume、隔离、配置校验、reducer、修复恢复和子图 checkpoint |

运行全部单元/集成测试：

```powershell
python -m pytest tests/ -q
```

真实三服务烟测会为每个 adapter 分配空闲端口和独立 SQLite/output，启动 Uvicorn、执行生成的 pytest，并在结束后关闭服务：

```powershell
python scripts/smoke_langgraph.py
```

烟测产物保留在 `artifacts/smoke-<adapter>-<id>/`，包含服务日志、数据库、报告和证据。

GitHub Actions 配置见 [`.github/workflows/tests.yml`](.github/workflows/tests.yml)。

当前 workflow 实际在针对 `main` 的 pull request 和 `main` push 上安装 Python 3.13 依赖、启动 Mini Shop、运行完整测试并上传 Allure 原始结果；main push 成功后使用 Java 21 和 Node.js 24 生成 Allure HTML 并部署 GitHub Pages。

本地 Allure 与 Docker：

```powershell
npm ci
.\scripts\run_allure.ps1

docker compose up --build --abort-on-container-exit
```

当前 Docker Compose 只编排默认 Mini Shop，不替代三 adapter 的 LangGraph 烟测。

## 安全边界

- HTTP 目标仅允许 `127.0.0.1` / `localhost`（hostname 精确白名单）
- 数据库观察者仅接受显式 SQLite 测试库，禁止生产库
- Agent 不执行任意 shell / SQL；删除、支付等高风险断言不自动放宽
- Token、密码、连接串写入证据前递归脱敏；缺证据不报 PASS
- 自动清理只处理 adapter 配置的 `agent_` 前缀测试账号及明确子记录，防止误删真实账号
- LLM 最多读取需求文档前 24,000 字符，超出部分标记截断；超长文档应拆分
- 默认 checkpoint 仅在当前进程有效；节点恢复不会回滚外部 HTTP/数据库副作用

## Roadmap

- [x] **V1** 最小可用闭环：OpenAPI → 用例 → pytest → 契约门禁 → 执行报告
- [x] **V2** 多 Agent 审核回路 + 受限修复 + 结构化日志 + 多 adapter
- [ ] **V3** 并发下单防超卖、幂等性、金额 Decimal 化、PostgreSQL + Alembic
- [ ] **V4** Web 平台化：多项目、历史趋势、审批流、Prompt/模型版本管理
