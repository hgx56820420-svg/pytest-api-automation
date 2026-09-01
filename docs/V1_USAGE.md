# V1 使用说明

V1 使用确定性 Python 工具跑通以下闭环。当前 Mini Shop 会生成 30 条场景用例，覆盖 14 个 OpenAPI operation 和原有手写测试中的成功、失败、边界及隔离场景：

```text
OpenAPI -> 规范化需求 JSON -> 用例 JSON -> 覆盖审核 -> pytest
        -> 运行时契约检查 -> HTTP/数据库证据 -> 执行报告
```

V1 暂不使用 LangChain，也不启用 RAG 检索。`docs/rag/` 中的规则已经固化到 V1 规划器和审核规则，后续版本再接入动态检索。

## 1. 启动独立测试服务

PowerShell：

```powershell
$env:APP_DATABASE_URL = "sqlite:///./agent-v1.db"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

只允许使用本地测试服务和独立 SQLite 数据库。不要连接生产服务或生产数据库。

## 2. 一次运行完整流水线

另开终端：

```powershell
python -m api_agent pipeline `
  --openapi http://127.0.0.1:8010/openapi.json `
  --requirements-md docs/MINI_SHOP_API_REQUIREMENTS.md `
  --base-url http://127.0.0.1:8010 `
  --database-url sqlite:///./agent-v1.db `
  --output artifacts/v1
```

## 3. 分阶段运行

只生成需求、用例、审核结果和 pytest：

```powershell
python -m api_agent generate `
  --openapi http://127.0.0.1:8010/openapi.json `
  --requirements-md docs/MINI_SHOP_API_REQUIREMENTS.md `
  --output artifacts/v1
```

检查运行时契约：

```powershell
python -m api_agent check `
  --runtime-openapi http://127.0.0.1:8010/openapi.json `
  --output artifacts/v1
```

执行已审核脚本：

```powershell
python -m api_agent run `
  --base-url http://127.0.0.1:8010 `
  --database-url sqlite:///./agent-v1.db `
  --output artifacts/v1
```

## 4. 输出

```text
artifacts/v1/
  normalized-requirement.json
  requirement-review.json
  test-cases.json
  coverage-report.json
  script-review.json
  contract-diff.json
  execution-report.json
  schemas/
  generated-tests/test_generated_api.py
  evidence/<run_id>/*.json
  junit.xml
  allure-results/
  pytest.stdout.log
  pytest.stderr.log
```

`execution-report.json` 是机器事实源。Allure 是展示层，可以继续使用项目已有脚本生成 HTML 报告。

## 5. V1 安全边界

- HTTP 目标只允许 `127.0.0.1` 或 `localhost`。
- DatabaseObserver 只接受显式 SQLite URL。
- 生成脚本不能使用任意 shell 或自由 SQL。
- 契约出现 breaking change 时阻断执行。
- 缺少证据时报告 `INCONCLUSIVE`，不报告虚假的成功。
