# pytest-api-automation

接口业务测试框架：从需求文档设计用例，用 pytest 写测试，不只看状态码，还检查数据库里库存、余额、订单状态有没有按需求变化。AI 负责起草用例和脚本，确定性脚本负责校验，人负责审核。

```
需求文档 + OpenAPI 契约
   │  skill/api-test-design（Agent 起草）
   ▼
cases/<service>.yaml   规则（附需求原文）→ 覆盖矩阵（7 维）→ 测试点 → 用例
   │  人审测试点、回答待确认问题；check_cases.py 校验整条链
   ▼
tests/<service>/*.py   pytest + api_agent 插件（api / db / admin fixture）
   │  check_tests.py 拦截弱断言；每个响应按契约校验；失败自动留证据
   ▼
CI   PR：oasdiff 契约 → check_cases → check_tests → pytest      每晚：mutmut 变异测试
```

## 解决什么问题

| 问题 | 做法 |
|---|---|
| 接口返回成功，业务其实错了（库存没扣、余额多扣、失败留下脏数据） | `db` fixture 只读查库，失败路径必须断言"数据库没变" |
| 测试失败了不知道原因 | 每条测试自动记录请求、响应、`X-Request-ID`、数据库查询结果，失败时附在报告里 |
| AI 生成的用例编造规则、只测正常路径 | 每条规则必须引用需求原文（脚本逐字核对）；7 维覆盖矩阵逼出异常、权限、并发；需求没写的变成问题，不让 AI 猜 |
| AI 写的测试断言很弱 | `check_tests.py` 静态拦截无断言、常量断言、吞异常；mutmut 改坏业务代码，看测试能不能发现 |
| 接口悄悄改了，调用方不知道 | 契约基线进 git，oasdiff 拦截破坏性变更；插件按契约校验每个真实响应 |

## 实测结果

在仓库自带的三个演示服务上（商城、图书借阅、会议室预约）：

- **测试设计**：只给需求文档，按 Skill 盲测生成商城用例 112 条，其中 85 条是手写测试没有覆盖的；对照"必问清单"又提出 19 个需求没写清楚的问题（金额精度、重复下单、未支付订单占库存等）。
- **并发测试发现 5 个真实缺陷**：库存 5 时 10 个并发下单全部成功（超卖）；只能用 1 次的优惠券被并发用了 4 次；并发注册、并发建券、并发加购各返回 500。已修复为条件 UPDATE。
- **契约校验发现漂移**：时间字段不带时区，不符合 OpenAPI `date-time`；会议室冲突检查因此按墙上时间比较，跨时区预约会漏判冲突。已修复。
- **变异测试发现测试漏洞**：去掉"购物车只查当前用户"的条件后原有测试全部通过；补测试后商城核心业务 442 个变异体全部被杀死。
- **需求缺陷**：原需求允许任何登录用户创建优惠券，可以 100% 折扣 0 元下单；先改需求加管理员角色，再改代码。

当前状态：业务测试 43 条、工具自身测试 37 条，全部通过；商城 108 条接口用例中 33 条已自动化（30%）。

## 仓库结构

```
api_agent/   pytest 插件：fixture、响应契约校验、失败证据、安全护栏（约 360 行）
skill/       api-test-design：测试设计四阶段 + 必问清单
cases/       测试设计产物（YAML，在 PR 里人审）
contracts/   接口基线（OpenAPI），接口有意变更时随 PR 更新
scripts/     export_openapi.py、check_cases.py、check_tests.py
tests/       业务测试按服务分目录；tests/plugin、tests/scripts 是工具自身的测试
examples/    被测演示服务：mini_shop、library、meeting
docs/        三个演示服务的需求文档
```

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip install -e . --no-deps
.\.venv\Scripts\python -m pytest
```

`tests/conftest.py` 会用临时数据库自动启动三个演示服务，不需要手动起服务。

## 写一条测试

```python
import pytest

@pytest.mark.case("TC-ORDER-007")            # 关联 cases/mini_shop.yaml 里的用例
def test_stock_boundary(api, db, create_product):
    product = create_product(price=1, stock=3)
    buyer = api.register()                    # 随机新用户，测试之间互不影响
    before = db.row("products", product["id"])["stock"]

    response = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 4})

    assert response.status_code == 400
    assert db.row("products", product["id"])["stock"] == before   # 失败时库存不能变
```

| fixture | 作用 |
|---|---|
| `api` | HTTP 客户端：`get/post/put/delete`、`register()`、`login()`；路径必须以 `/` 开头，只能发往被测服务；配置契约后每个响应都会校验 |
| `db` | 只读查询：`row(table, id)`、`rows(table, **where)`、`count(table, **where)`；表名列名做标识符校验 |
| `admin` | 用配置里的管理员账号登录后的用户 |

## 质量门禁

| 检查 | 时机 | 拦住什么 |
|---|---|---|
| `oasdiff breaking` | PR | 删字段、改类型、删接口等破坏性接口变更 |
| `scripts/check_cases.py` | PR | 引用不到需求原文、接口不在契约里、待确认问题没回答、规则/矩阵/测试点/用例断链 |
| `scripts/check_tests.py` | PR | 无断言、常量断言、吞异常、无理由 skip、缺用例编号、该查数据库的用例没查 |
| `pytest --api-contract` | PR | 业务结果不符合预期；响应的状态码、字段和契约不一致 |
| `mutmut` | 每晚 | 改坏业务代码后测试仍然通过（断言不够强） |

## 指向真实测试环境

不需要 `tests/conftest.py` 的演示服务，直接传参数或环境变量：

| 参数 | 环境变量 | 说明 |
|---|---|---|
| `--api-base-url` | `API_BASE_URL` | 被测服务地址 |
| `--api-db-url` | `API_DB_URL` | 测试库连接，请使用只读账号；SQLite 自动以只读模式打开 |
| `--api-admin-user` / `--api-admin-password` | `API_ADMIN_USERNAME` / `API_ADMIN_PASSWORD` | 测试管理员账号 |
| `--api-contract` | `API_CONTRACT` | 接口基线 OpenAPI JSON，需要 `pip install api-agent[contract]` |
| `--api-evidence-dir` | | 证据目录，默认 `artifacts/evidence` |

失败证据写入 `artifacts/evidence/<测试名>.json`，密码、token 自动替换为 `***`；每次运行结束写出 `case-results.json`（用例编号 → 结果）。

## 接口兼容性检查

```powershell
.\.venv\Scripts\python scripts/export_openapi.py contracts         # 第一次：建立基线，随 PR 提交并人审
.\.venv\Scripts\python scripts/export_openapi.py build/openapi     # 之后：导出当前代码的接口
docker run --rm -v "${PWD}:/w" -w /w tufin/oasdiff:v1.37.0 breaking contracts/mini_shop.openapi.json build/openapi/mini_shop.openapi.json --fail-on ERR
```

## 变异测试

只能在 Linux / WSL 上跑（mutmut 需要 fork），服务要在测试进程内启动：

```bash
DEMO_IN_PROCESS=1 mutmut run --max-children 4
mutmut results
```

mutmut 不变异带装饰器的函数，所以路由函数只做参数注入，业务逻辑放在普通函数里（如 `place_order`、`pay`）。

## 已知限制

- 插件的 `register()` / `login()` 假设 JSON 登录接口和 `access_token` 字段；表单登录或响应体包一层 `code/data` 的项目需要自己写登录 fixture。
- 业务失败返回 HTTP 200 + 响应体错误码的项目，契约校验只能检查结构，业务结果要断言响应体。
- 金额目前用 float 存储，精度问题已列为待确认问题，尚未修复。
- `cases/mini_shop.yaml` 还有待确认问题未回答，`check_cases.py` 会报错，这是有意的：问题不回答就不能合入。

## 历史

v0.1 是基于 LangGraph 的多 Agent 编排版本。v0.2 重写为 pytest 插件：去掉 LangGraph 和内置 LLM 调用，用例生成交给 Agent + Skill，执行和校验全部是确定性代码，代码量从约 5900 行降到约 660 行（插件 + 脚本）。
