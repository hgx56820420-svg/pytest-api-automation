"""Deterministic Markdown requirement extraction and OpenAPI review."""

from __future__ import annotations

import re
from pathlib import Path

from api_agent.models import NormalizedRequirement, RequirementEntry, RequirementReview
from api_agent.openapi import canonical_hash

# 需求 ID 支持多段前缀（如 REQ-AUTH-001、REQ-LIB-META-001）
REQUIREMENT_HEADING = re.compile(
    r"^####\s+(?P<id>REQ-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d+)\s+`(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(?P<path>[^`]+)`(?:\s+(?P<title>.*))?$"
)

BUSINESS_RULE_MARKERS = {
    "inventory_change": ("库存", "减少"),
    "balance_change": ("余额", "减少"),
    "cancel_restores_state": ("取消", "恢复"),
    "failure_has_no_side_effect": ("失败", "不得变化"),
    "order_state_machine": ("订单状态机", "终态"),
    "cross_user_isolation": ("跨用户", "隔离"),
    "transaction_atomicity": ("原子性", "全部成功或全部失败"),
}

SCENARIO_MARKERS = {
    "register_boundaries": ("用户名边界", "422"),
    "duplicate_register": ("重复用户名", "409"),
    "wrong_password": ("密码错误", "401"),
    "missing_token": ("Token 缺失", "401"),
    "create_product_missing_token": ("未认证", "创建商品"),
    "get_product_not_found": ("商品不存在", "404"),
    "create_product_invalid_price": ("price > 0", "422"),
    "list_products_invalid_pagination": ("分页参数", "422"),
    "order_product_not_found": ("商品不存在", "下单"),
    "order_invalid_quantity": ("quantity", "1–100"),
    "cancel_paid": ("取消已支付订单", "409"),
    "cancel_cancelled": ("已取消订单", "不能再次取消"),
    "other_user_order": ("其他用户", "订单", "404"),
    "insufficient_stock": ("库存不足", "不得变化"),
    "insufficient_balance": ("余额不足", "不得变化"),
    "pay_twice": ("重复支付", "409"),
}


def review_markdown_requirements(path: Path, requirement: NormalizedRequirement) -> RequirementReview:
    text = path.read_text(encoding="utf-8-sig")
    entries: list[RequirementEntry] = []
    issues: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    openapi_routes = {(operation.method, operation.path): operation for operation in requirement.operations}
    operation_mapping: dict[str, str] = {}

    for line_number, line in enumerate(text.splitlines(), start=1):
        match = REQUIREMENT_HEADING.match(line.strip())
        if not match:
            continue
        requirement_id = match.group("id")
        method = match.group("method")
        route = match.group("path").strip()
        if requirement_id in seen_ids:
            issues.append(f"Duplicate requirement ID: {requirement_id}")
        seen_ids.add(requirement_id)
        operation = openapi_routes.get((method, route))
        entry = RequirementEntry(
            requirement_id=requirement_id,
            method=method,
            path=route,
            title=(match.group("title") or "").strip(),
            line=line_number,
            operation_id=operation.operation_id if operation else None,
        )
        entries.append(entry)
        if operation is None:
            issues.append(f"{requirement_id} references an unknown operation: {method} {route}")
        elif operation.operation_id in operation_mapping:
            issues.append(f"Multiple requirements map to operation {operation.operation_id}")
        else:
            operation_mapping[operation.operation_id] = requirement_id

    for operation in requirement.operations:
        if operation.operation_id not in operation_mapping:
            issues.append(f"OpenAPI operation is missing from requirements: {operation.method} {operation.path}")

    detected = [rule_id for rule_id, markers in BUSINESS_RULE_MARKERS.items() if all(marker in text for marker in markers)]
    detected_scenarios = [scenario for scenario, markers in SCENARIO_MARKERS.items() if all(marker in text for marker in markers)]
    missing_rules = sorted(set(BUSINESS_RULE_MARKERS) - set(detected))
    if missing_rules:
        warnings.append("Business rules not detected: " + ", ".join(missing_rules))
    if not entries:
        issues.append("No REQ-* API headings were found in the Markdown document")

    return RequirementReview(
        source=str(path),
        source_hash=canonical_hash(text),
        decision="approved" if not issues else "needs_revision",
        entries=entries,
        operation_mapping=operation_mapping,
        detected_business_rules=detected,
        detected_scenarios=detected_scenarios,
        issues=issues,
        warnings=warnings,
    )
