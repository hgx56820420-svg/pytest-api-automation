"""响应契约校验：每个响应都要符合接口基线（OpenAPI）。

检查三件事：
1. 接口存在于契约里；
2. 返回的状态码在契约里有声明；
3. 响应体符合该状态码的 schema（交给 Schemathesis），并且不含契约里没有的字段。

第 3 点里的"多余字段"单独检查，因为 FastAPI 生成的 schema 默认允许额外字段，
后端悄悄加的字段用普通的 schema 校验发现不了。
"""

import json
from pathlib import Path
from typing import Any


class ContractViolation(AssertionError):
    """响应和接口基线不一致。"""


class ContractValidator:
    def __init__(self, contract_path: str | Path):
        import schemathesis  # 只有配置了契约才需要这个依赖

        self.path = Path(contract_path)
        self.raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.schema = schemathesis.openapi.from_dict(self.raw)

    def check(self, method: str, path: str, response) -> None:
        operation = self.schema.find_operation_by_path(method.upper(), path.split("?")[0])
        if operation is None:
            raise ContractViolation(f"{method} {path} is not in contract {self.path.name}")
        label = f"{method.upper()} {operation.path}"
        responses = operation.definition.raw.get("responses", {})
        status = str(response.status_code)
        if status not in responses and "default" not in responses:
            raise ContractViolation(f"{label} returned {status}, contract documents {sorted(responses)}")
        try:
            operation.validate_response(response)
        except (AssertionError, BaseExceptionGroup) as exc:
            raise ContractViolation(f"{label} {status} body does not match contract: {exc}") from None
        body_schema = (
            responses.get(status, responses.get("default", {})).get("content", {}).get("application/json", {}).get("schema")
        )
        if body_schema is not None and response.content:
            extra = self._extra_fields(response.json(), body_schema, "$")
            if extra:
                raise ContractViolation(f"{label} {status} returned fields not in contract: {', '.join(extra)}")

    def _resolve(self, schema: dict) -> dict:
        while "$ref" in schema:
            node: Any = self.raw
            for part in schema["$ref"].lstrip("#/").split("/"):
                node = node[part]
            schema = node
        return schema

    def _extra_fields(self, value: Any, schema: dict, where: str) -> list[str]:
        schema = self._resolve(schema)
        variants = schema.get("anyOf") or schema.get("oneOf")
        if variants:
            # 取多余字段最少的分支，例如 "对象 或 null"
            return min((self._extra_fields(value, v, where) for v in variants), key=len)
        if isinstance(value, dict) and "properties" in schema:
            props = schema["properties"]
            extra = [f"{where}.{key}" for key in value if key not in props]
            for key, item in value.items():
                if key in props:
                    extra += self._extra_fields(item, props[key], f"{where}.{key}")
            return extra
        if isinstance(value, list) and "items" in schema:
            return [e for i, item in enumerate(value) for e in self._extra_fields(item, schema["items"], f"{where}[{i}]")]
        return []
