# 经验记录

## 环境

- PowerShell 5.1 不支持 `&&`。
- 项目使用端口 `8010`，不要默认改回 `8000`。
- Python 3.14 对依赖版本敏感，按 `requirements.txt` 安装，不要随意降低 pydantic。
- 测试必须使用 `.venv\Scripts\python.exe`，避免跑到系统 Python。

## 测试

- 反向验证很重要：把应失败的输入改成合法值，确认测试确实能区分业务场景。
- 路径参数需要用真实 ID 拼接，例如 `f"/api/orders/{order_id}/pay"`；字面量 `{order_id}` 会导致 422。
- 第二次请求必须断言第二次响应，不能误用第一次请求的 response 变量。
