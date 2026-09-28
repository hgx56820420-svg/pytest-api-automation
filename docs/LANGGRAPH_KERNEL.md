# LangGraph 内核拆分

仓库原本已用 `StateGraph` 连接节点，但修复账本保存在 `self.repair`，节点手工拼接历史列表，LLM 还有两层重试（SDK 重试加 `for/sleep`）。这次把执行状态、历史合并和模型重试交给 LangGraph。

## 模块与职责

| 模块 | 职责 | LangGraph 能力 |
|---|---|---|
| `api_agent/graph_state.py` | 运行标识、修复记录、升级状态、无进展签名、LLM 规则、审计事件 | `TypedDict` state、`Annotated[..., operator.add]` reducers |
| `api_agent/workflow_graph.py` | 主流程节点和审核分支 | `StateGraph`、普通边、条件边 |
| `api_agent/workflow.py` | 绑定业务工具、启动/恢复运行、导出业务产物 | `compile(checkpointer=...)`、`invoke(None)`、静态断点 |
| `api_agent/analyst_graph.py` | 结构化提取、JSON 降级、ID 护栏 | 子图、`RetryPolicy`、`Command`、节点 `error_handler` |
| `api_agent/repair.py` | 修复 diff 的纯函数；保留旧接口供兼容调用 | 不再持有主图的执行状态 |

需求解析、契约比较、覆盖审核、修复预算、数据库断言 DSL、HTTP 执行、证据真实性检查仍是领域逻辑。LangGraph 负责调度和状态存储，不决定哪些业务断言应该通过。

`messages` 是经过 Pydantic 校验的业务消息信封，因此使用列表 reducer；它不是模型对话，不使用面向聊天消息的 `add_messages`。节点只返回新增事件，避免恢复时重复追加整段历史，并允许独立节点合并并行更新。

## 运行和恢复

原 CLI 保持可用：

```powershell
python -m api_agent v2 --openapi http://127.0.0.1:8010/openapi.json --requirements-md docs/MINI_SHOP_API_REQUIREMENTS.md --base-url http://127.0.0.1:8010 --database-url sqlite:///./agent-v2.db --output artifacts/v2
```

Python API 支持显式传入 LangGraph checkpointer 和执行前断点：

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
paused_state = workflow.invoke()  # 完成审核后停在 execute 前，不产生最终决策
snapshot = workflow.graph.get_state({"configurable": {"thread_id": settings["run_id"]}})
assert snapshot.next == ("execute",)

# 确认产物后，使用同一 saver、run_id、目录和配置创建新实例。
resumed = V2Workflow(**settings)
final_state = resumed.resume()
```

- 默认使用 `InMemorySaver`，只在当前进程内保存 checkpoint。跨进程恢复需要调用者传入 LangGraph 的持久化 saver（例如单独安装的 SQLite/Postgres saver），并管理其生命周期；本次没有新增 CLI 跨进程恢复命令。
- `thread_id` 必须等于 `run_id`，防止状态与证据标识串台。`resume()` 检查配置摘要；已经完成的运行直接返回最终状态。重复 `invoke()` 会提示使用 `resume()` 或新的运行 ID。
- 图中完成的步骤、修复预算、升级状态和无进展签名均从 checkpoint 恢复。`repair-history.json` 是审计导出，运行时不从该文件恢复计数。
- 每个并发运行应使用不同 `run_id` **和产物目录**。业务工具仍通过产物文件传递契约、用例和报告；必须保留目录且不能在暂停期间覆盖这些文件。本次并未把产物内容全部迁入 checkpoint。
- 恢复是节点级的：失败节点可能重新执行。HTTP 请求和数据库写入不会被 LangGraph 回滚；`execute` 中断后恢复可能重发请求。需要幂等请求、隔离测试库，或人工确认外部状态。不要对整个执行节点添加自动 `RetryPolicy`。
- 业务审核最终的 `NEEDS_HUMAN`/`FAIL` 仍按原 CLI 约定输出报告；示例的静态断点是额外的编排能力，不会把业务失败自动批准。

## 模型调用子图

结构化提取成功后进入 ID 护栏；重试耗尽或网关不支持工具模式时，通过 `Command` 进入文本 JSON 节点，再经过相同护栏。

- 结构化节点最多 4 次尝试；文本节点最多 3 次尝试。
- 使用 `RetryPolicy` 指数退避（初始 2 秒、上限 20 秒并带 jitter）。可在 `build_analyst_graph` 传入策略进行部署调整或无延迟测试。
- 网络错误、限流/服务端错误、无结构化输出、Pydantic/解析失败可重试。认证和权限错误直接抛出。
- SDK 的 `max_retries=0`，避免与图重试叠乘。
- 模型失败继续沿用原有行为：记录问题，继续确定性覆盖；不把旧运行遗留的 `llm-rules.json` 当成当前模型结果。

## 兼容与验证

V1 命令、V2 CLI 退出码、业务消息和报告 schema 保留。`workflow.repair` 现在是从图状态构造的兼容快照；修改这个对象不会改变运行中的图。转人工记录保留在账本中，但不计入自动修复次数。

```powershell
python -m pytest tests/ -q
python scripts/smoke_langgraph.py
```

单测覆盖原三个 adapter、节点恢复、预算/无进展状态恢复、线程隔离、reducer 合并、遗留规则隔离、模型重试和降级。smoke 脚本启动三个独立本地服务，为每次验证创建新的 SQLite 库和输出目录，执行真实生成的 pytest 用例，保留服务日志、报告和证据，并在结束后关闭服务进程。
