"""Deterministic OpenAPI loading, reference expansion and normalization."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from api_agent.models import NormalizedRequirement, OperationSpec, ParameterSpec

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_openapi(source: str, timeout: float = 10.0) -> dict[str, Any]:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        response = requests.get(source, timeout=timeout)
        response.raise_for_status()
        document = response.json()
    else:
        path = Path(source)
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() not in {".json", ""}:
            raise ValueError("V1 supports OpenAPI JSON only; convert YAML to JSON first")
        document = json.loads(text)
    if not isinstance(document, dict) or "openapi" not in document or "paths" not in document:
        raise ValueError("Input is not an OpenAPI document")
    return document


def resolve_ref(document: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, dict) or "$ref" not in value:
        return deepcopy(value)
    reference = value["$ref"]
    if not reference.startswith("#/"):
        raise ValueError(f"External OpenAPI references are not supported in V1: {reference}")
    target: Any = document
    for segment in reference[2:].split("/"):
        target = target[segment.replace("~1", "/").replace("~0", "~")]
    return resolve_refs(document, target)


def resolve_refs(document: dict[str, Any], value: Any) -> Any:
    if isinstance(value, dict):
        if "$ref" in value:
            return resolve_ref(document, value)
        return {key: resolve_refs(document, child) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_refs(document, child) for child in value]
    return value


def normalize_openapi(document: dict[str, Any], source: str) -> NormalizedRequirement:
    operations: list[OperationSpec] = []
    issues: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()

    for path, path_item in sorted(document.get("paths", {}).items()):
        shared_parameters = path_item.get("parameters", []) if isinstance(path_item, dict) else []
        for method, raw_operation in sorted(path_item.items()):
            if method.lower() not in HTTP_METHODS or not isinstance(raw_operation, dict):
                continue
            operation_id = raw_operation.get("operationId") or _fallback_operation_id(method, path)
            if operation_id in seen_ids:
                issues.append(f"Duplicate operationId: {operation_id}")
            seen_ids.add(operation_id)
            parameters: list[ParameterSpec] = []
            for raw_parameter in [*shared_parameters, *raw_operation.get("parameters", [])]:
                parameter = resolve_ref(document, raw_parameter)
                parameters.append(
                    ParameterSpec(
                        name=parameter["name"],
                        location=parameter["in"],
                        required=bool(parameter.get("required", False)),
                        schema=resolve_refs(document, parameter.get("schema", {})),
                    )
                )
            request_schema = _content_schema(document, raw_operation.get("requestBody", {}).get("content", {}))
            responses: dict[str, dict[str, Any]] = {}
            for status_code, response in raw_operation.get("responses", {}).items():
                resolved_response = resolve_ref(document, response)
                schema = _content_schema(document, resolved_response.get("content", {}))
                responses[str(status_code)] = schema or {}
            if not responses:
                warnings.append(f"{operation_id} has no documented responses")
            operations.append(
                OperationSpec(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    summary=raw_operation.get("summary", ""),
                    tags=raw_operation.get("tags", []),
                    security_required=bool(raw_operation.get("security", document.get("security", []))),
                    parameters=parameters,
                    request_schema=request_schema,
                    responses=responses,
                    source_ref=f"#/paths/{_escape_pointer(path)}/{method.lower()}",
                )
            )

    info = document.get("info", {})
    return NormalizedRequirement(
        title=info.get("title", "Untitled API"),
        api_version=str(info.get("version", "unknown")),
        openapi_version=str(document.get("openapi", "unknown")),
        source=source,
        source_hash=canonical_hash(document),
        operations=operations,
        issues=issues,
        warnings=warnings,
    )


def _content_schema(document: dict[str, Any], content: dict[str, Any]) -> dict[str, Any] | None:
    media = content.get("application/json") if isinstance(content, dict) else None
    if not media or "schema" not in media:
        return None
    return resolve_refs(document, media["schema"])


def _fallback_operation_id(method: str, path: str) -> str:
    cleaned = path.strip("/").replace("/", "_").replace("{", "").replace("}", "") or "root"
    return f"{method.lower()}_{cleaned}"


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")

