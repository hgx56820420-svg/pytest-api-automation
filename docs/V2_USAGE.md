# V2 使用说明

V2 在 V1 确定性闭环的基础上，引入 **LangGraph 编排的多 Agent 工作流**，并以需求文档（Markdown）为主要输入，OpenAPI 退为契约校验基准：

```text
需求文档 (MD)
  -> 需求解析 Agent    从文档抽取接口清单、认证要求、业务规则 -> parsed-requirement.json
  -> 需求审核 Agent    与 OpenAPI 契约交叉对齐               -> requirement-review.json
  -> 用例设计 Agent    生成标准用例 JSON                     -> test-cases.json
  -> 覆盖审核 Agent    审核覆盖缺口，缺口回传用例 Agent       -> coverage-report.json
  -> 脚本生成 Agent    用例 JSON -> pytest                   -> generated-tests/
  -> 脚本审核 Agent    静态检查，不通过回传脚本 Agent         -> script-review.json
  -> 契约门禁          破坏性变化先 interrupt，人工 approve 后选择性再生 -> contract-diff.json
  -> 执行 Agent        真实 HTTP + 数据库证据                 -> execution-report.json
  -> 结果审核 Agent    证据链与 request_id 关联校验           -> result-review.json
  -> 收尾              决策 + 失败现场保留                    -> workflow-report.json
```

Agent 之间只传递经过 Pydantic 校验的消息信封（`AgentMessage`：`from/to/topic/payload_ref/payload_hash`），payload 以产物文件为引用，可独立审计。除可选的 `analyze_requirements` 外，节点主要是确定性实现；该节点在显式传入 `--llm-analysis` 且 LLM 配置完整时使用 LangChain 结构化调用。LLM SDK 自动重试已关闭，模型调用次数和退避由脚本显式控制：结构化调用最多 4 次，失败后纯文本 JSON 兜底最多 3 次。工作流使用 SQLite checkpoint 保存状态，人工升级进入 `interrupt`，可用同一 `run_id` 从断点恢复。

## 1. 启动独立测试服务

PowerShell：

```powershell
$env:APP_DATABASE_URL = "sqlite:///./agent-v2.db"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

只允许本地测试服务和独立 SQLite 数据库。

## 2. 一次运行完整工作流

另开终端：

```powershell
python -m api_agent v2 `
  --openapi http://127.0.0.1:8010/openapi.json `
  --requirements-md docs/MINI_SHOP_API_REQUIREMENTS.md `
  --base-url http://127.0.0.1:8010 `
  --database-url sqlite:///./agent-v2.db `
  --output artifacts/v2 `
  --max-repair-attempts 2
```

退出码：`0` PASS；`1` FAIL（业务断言失败，转人工定位）；`3` NEEDS_HUMAN（修复预算耗尽或审核无法收敛）。

如果输出状态为 `WAITING_HUMAN`，命令会打印中断原因和恢复命令。人工决定后使用同一个 `run_id` 恢复：

```powershell
python -m api_agent v2-resume `
  --run-id run-xxxxxxxxxxxx `
  --output artifacts/v2 `
  --openapi http://127.0.0.1:8010/openapi.json `
  --requirements-md docs/MINI_SHOP_API_REQUIREMENTS.md `
  --base-url http://127.0.0.1:8010 `
  --database-url sqlite:///./agent-v2.db `
  --adapter mini_shop `
  --max-repair-attempts 2 `
  --action approve `
  --comment "确认此次契约变更"
```

`--action` 可选 `approve`、`retry`、`reject`。`approve` 会继续当前人工确认的流程，`retry` 会回到待修复节点，`reject` 会结束为 `NEEDS_HUMAN`。

## 3. 输出

```text
artifacts/v2/
  parsed-requirement.json      需求解析 Agent 产物（V2 新增）
  normalized-requirement.json  OpenAPI 契约基线
  requirement-review.json      需求审核
  test-cases.json              用例 JSON
  coverage-report.json         覆盖审核
  script-review.json           脚本审核
  contract-diff.json           契约门禁
  execution-report.json        机器事实源
  result-review.json           结果审核（V2 新增）
  workflow-report.json         工作流轨迹与状态（RUNNING/WAITING_HUMAN/COMPLETED）
  repair-history.json          受限修复留档，含 before/after diff（V2 新增）
  human-review.json             人工中断请求与决定
  failure-scene.json            失败现场索引（含升级原因）
  workflow-input.json           首次运行参数快照（恢复时校验，数据库 URL 仅存 hash）
  llm-rules.json                可选 LangChain 需求分析产物
  llm-retry-history.json        LLM 每次尝试的阶段、次数和失败原因
  workflow-checkpoints.sqlite3  LangGraph 断点数据库
  evidence/<run_id>/agent-log.jsonl   结构化日志（V2 新增）
  evidence/<run_id>/<case>.json       每用例证据，含 request_id
  generated-tests/ allure-results/ junit.xml pytest.*.log
```

## 4. V2 行为约定

### 审核路由

审核失败不重跑全流程，而是路由回正确的前置 Agent：需求审核失败回到解析 Agent；覆盖审核失败回到用例设计 Agent；脚本审核失败回到脚本生成 Agent；结果审核失败回到执行 Agent。

### 受限修复

- 自动修复默认最多 2 次（`--max-repair-attempts`）。
- 修复重跑后产物签名与上次相同（无进展）时立即转人工，不烧完预算。
- 每次修复在 `repair-history.json` 留档 attempt、trigger、before/after 摘要和 unified diff。
- 每次重试计划和修复结果都保留失败原因；如果仍无进展或达到预算上限，人工中断原因包含原始失败原因或最后一次失败原因。
- 超限或无进展后工作流进入 `human_review` 节点，通过 LangGraph `interrupt` 等待人工审核；状态保存在 `workflow-checkpoints.sqlite3`，恢复不需要从头重跑。
- `workflow-report.json` 在运行中先写 `RUNNING`，中断时写 `WAITING_HUMAN`，最终才写 `COMPLETED`；因此即使尚未恢复，也能看到当前轨迹和失败现场。

### 契约变化的选择性再生

契约门禁发现破坏性变化时，先通过 `interrupt` 请求人工确认；只有 `approve` 后才重新生成受影响 operation 的用例并合并保留未受影响用例，再生过程记入 `repair-history.json`。普通审核失败不能用 `approve` 绕过，只能 `retry` 或 `reject`。

### 证据关联与日志

- 每个测试请求携带唯一 `X-Request-ID`，写入用例证据与 `agent-log.jsonl`。
- `agent-log.jsonl` 每条记录含 `run_id/agent/case_id/operation_id/request_id`，可四方关联。
- 结果审核校验证据存在性和 request_id 关联；执行报告声称 PASS 但审计有缺口时强制进入修复回路，不放行。
- 失败时 `failure-scene.json` 记录失败用例、问题、证据目录与日志路径。

### Allure

生成脚本把每条用例证据 JSON 作为 Allure 附件；`.\scripts\run_allure.ps1` 照常生成报告。

## 5. 多被测对象（adapter 机制）

V2 通过 adapter 把"业务适配层"从框架中分离，一个 adapter 绑定一个被测服务：

| Adapter | 被测服务 | 启动方式 | 默认端口 |
|---|---|---|---|
| `mini_shop`（默认） | `app/` Mini Shop API | `python -m uvicorn app.main:app --port 8010` | 8010 |
| `library` | `services/library/` Library API | `$env:LIB_DATABASE_URL=...; python -m uvicorn services.library.main:app --port 8020` | 8020 |
| `meeting` | `services/meeting/` Meeting Room API | `$env:MEETING_DATABASE_URL=...; python -m uvicorn services.meeting.main:app --port 8030` | 8030 |

每个 adapter 注册四样东西（见 `api_agent/adapters.py`）：operation 场景映射、负面用例模板、场景执行器类、数据库观察者类，以及需求解析用的通配认证规则。

library 二次验证：

```powershell
$env:LIB_DATABASE_URL = "sqlite:///./library-v2.db"
python -m uvicorn services.library.main:app --host 127.0.0.1 --port 8020
# 另开终端：
python -m api_agent v2 `
  --openapi http://127.0.0.1:8020/openapi.json `
  --requirements-md docs/LIBRARY_API_REQUIREMENTS.md `
  --base-url http://127.0.0.1:8020 `
  --database-url sqlite:///./library-v2.db `
  --output artifacts/library-v2 `
  --adapter library
```

接入新被测服务的步骤：新增 `DomainAdapter`（场景映射 + 用例模板 + Executor/Observer 子类）→ 编写 `docs/<领域>_API_REQUIREMENTS.md`（`#### REQ-XX-001` 标题格式）→ 以 `--adapter <name>` 运行。框架层（工作流、审核、修复、证据链）零改动。已有三个领域的验证经验：

- `mini_shop`：数量/余额副作用（库存、余额扣减与恢复）
- `library`：副本/押金副作用 + 借阅状态机（borrowed → returned）
- `meeting`：时间窗冲突规则（重叠 409、相邻时段放行）+ 金额快照

## 6. V2 安全边界

- HTTP 目标只允许 `127.0.0.1` 或 `localhost`；DatabaseObserver 只接受显式 SQLite URL。
- Agent 不执行任意 shell 或 SQL；自动修复不触碰删除、支付、退款等高风险断言。
- Token、密码、数据库连接串不进入报告或日志（executor 递归脱敏）。
- 缺少证据时结论为 `INCONCLUSIVE`，不得放宽为 PASS。

## 7. V1 命令兼容

V1 的 `generate` / `check` / `run` / `pipeline` 子命令保持不变，行为与 `docs/V1_USAGE.md` 一致。
