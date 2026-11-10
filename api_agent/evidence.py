"""证据记录：每条测试一份，记下发出的请求、收到的响应和数据库查询结果。"""

import re
from typing import Any

_SENSITIVE_KEY = re.compile(r"password|passwd|token|secret|authorization|api[_-]?key|cookie", re.IGNORECASE)
_MAX_TEXT = 2000
REDACTED = "***"


def redact(value: Any) -> Any:
    """递归脱敏：键名像密码/token/密钥的字段一律替换成 ***。"""
    if isinstance(value, dict):
        return {k: REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class Recorder:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def http(self, *, request_id: str, method: str, path: str, params: Any, body: Any, response) -> None:
        try:
            response_body: Any = redact(response.json())
        except ValueError:
            # 非 JSON 响应不知道里面有什么，只记长度，不记内容
            response_body = f"<non-JSON body, {len(response.content)} bytes>"
        self.entries.append(
            {
                "kind": "http",
                "request_id": request_id,
                "method": method,
                "path": path,
                "params": redact(params),
                "request_body": redact(body),
                "status": response.status_code,
                "response_body": _truncate(response_body),
            }
        )

    def db(self, *, query: str, params: dict[str, Any], result: Any) -> None:
        self.entries.append({"kind": "db", "query": query, "params": params, "result": redact(result)})


def _truncate(value: Any) -> Any:
    text = repr(value)
    return value if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + "...<truncated>"
