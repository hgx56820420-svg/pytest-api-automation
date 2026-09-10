"""Requirement Parser Agent (V2): extract interfaces and rules from Markdown.

This agent is the V2 entry point: the Markdown requirement document is the
primary source, while OpenAPI remains the machine contract used for review.
It is deterministic today; when an LLM is available this node can be swapped
for a LangChain structured-output call without changing the workflow graph.
"""

from __future__ import annotations

import re
from pathlib import Path

from api_agent.models import ParsedInterface, ParsedRequirement
from api_agent.openapi import canonical_hash
from api_agent.requirements import BUSINESS_RULE_MARKERS, REQUIREMENT_HEADING, SCENARIO_MARKERS

SUCCESS_CODE = re.compile(r"成功响应\s*`?(\d{3})`?")
SECTION_HEADING = re.compile(r"^#{1,3}\s+")

# Section-level blanket rules: text marker -> interfaces whose path starts with prefix.
BLANKET_AUTH = [
    ("所有订单接口均需要认证", "/api/orders"),
    ("所有购物车接口都需要 Bearer Token", "/api/cart"),
]


def parse_requirements(path: Path) -> ParsedRequirement:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    interfaces: list[ParsedInterface] = []
    issues: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    section: list[str] = []
    current: ParsedInterface | None = None

    def close_current() -> None:
        nonlocal current
        if current is None:
            return
        body = "\n".join(section)
        if "认证：需要" in body:
            current.auth_required = True
        current.expected_status_codes = [int(code) for code in SUCCESS_CODE.findall(body)] or [200]
        interfaces.append(current)
        current = None

    for line_number, line in enumerate(lines, start=1):
        if SECTION_HEADING.match(line):
            close_current()
            section = []
            continue
        match = REQUIREMENT_HEADING.match(line.strip())
        if not match:
            if current is not None:
                section.append(line)
            continue
        close_current()
        requirement_id = match.group("id")
        if requirement_id in seen_ids:
            issues.append(f"Duplicate requirement ID: {requirement_id}")
        seen_ids.add(requirement_id)
        current = ParsedInterface(
            requirement_id=requirement_id,
            method=match.group("method"),
            path=match.group("path").strip(),
            title=(match.group("title") or "").strip(),
            line=line_number,
        )
        section = []

    close_current()

    for marker, prefix in BLANKET_AUTH:
        if marker in text:
            for interface in interfaces:
                if interface.path.startswith(prefix):
                    interface.auth_required = True

    business_rules = [rule for rule, markers in BUSINESS_RULE_MARKERS.items() if all(marker in text for marker in markers)]
    scenarios = [scenario for scenario, markers in SCENARIO_MARKERS.items() if all(marker in text for marker in markers)]
    missing_rules = sorted(set(BUSINESS_RULE_MARKERS) - set(business_rules))
    if missing_rules:
        warnings.append("Business rules not detected: " + ", ".join(missing_rules))
    if not interfaces:
        issues.append("No REQ-* API headings were found in the Markdown document")

    title = ""
    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return ParsedRequirement(
        source=str(path),
        source_hash=canonical_hash(text),
        title=title,
        interfaces=interfaces,
        business_rules=business_rules,
        scenarios=scenarios,
        issues=issues,
        warnings=warnings,
    )
