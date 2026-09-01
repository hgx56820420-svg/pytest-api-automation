"""Structural OpenAPI drift detection used before generated tests run."""

from __future__ import annotations

from api_agent.models import ContractChange, ContractDiffReport, NormalizedRequirement, TestCaseDocument
from api_agent.openapi import canonical_hash


def compare_contracts(
    baseline: NormalizedRequirement,
    runtime: NormalizedRequirement,
    cases: TestCaseDocument,
) -> ContractDiffReport:
    baseline_ops = {item.operation_id: item for item in baseline.operations}
    runtime_ops = {item.operation_id: item for item in runtime.operations}
    case_map: dict[str, list[str]] = {}
    for case in cases.cases:
        case_map.setdefault(case.operation_id, []).append(case.case_id)

    changes: list[ContractChange] = []
    for operation_id, expected in baseline_ops.items():
        actual = runtime_ops.get(operation_id)
        affected = case_map.get(operation_id, [])
        if actual is None:
            changes.append(_change("breaking", "operation_removed", operation_id, "Operation is absent at runtime", affected))
            continue
        if (expected.method, expected.path) != (actual.method, actual.path):
            changes.append(_change("breaking", "route_changed", operation_id, f"Expected {expected.method} {expected.path}; got {actual.method} {actual.path}", affected))
        if expected.security_required != actual.security_required:
            changes.append(_change("breaking", "security_changed", operation_id, "Authentication requirement changed", affected))
        if canonical_hash([item.model_dump(by_alias=True) for item in expected.parameters]) != canonical_hash(
            [item.model_dump(by_alias=True) for item in actual.parameters]
        ):
            changes.append(_change("breaking", "parameters_changed", operation_id, "Parameters or constraints changed", affected))
        if canonical_hash(expected.request_schema) != canonical_hash(actual.request_schema):
            changes.append(_change("breaking", "request_schema_changed", operation_id, "Request schema changed", affected))
        expected_codes = set(expected.responses)
        actual_codes = set(actual.responses)
        if missing := expected_codes - actual_codes:
            changes.append(_change("breaking", "responses_removed", operation_id, f"Response codes removed: {sorted(missing)}", affected))
        if added := actual_codes - expected_codes:
            changes.append(_change("warning", "responses_added", operation_id, f"Response codes added: {sorted(added)}", affected))
        for code in expected_codes & actual_codes:
            if canonical_hash(expected.responses[code]) != canonical_hash(actual.responses[code]):
                changes.append(_change("breaking", "response_schema_changed", operation_id, f"Response schema changed for {code}", affected))

    for operation_id in runtime_ops.keys() - baseline_ops.keys():
        changes.append(_change("warning", "operation_added", operation_id, "New runtime operation is not covered by the baseline", []))

    status = "breaking" if any(item.severity == "breaking" for item in changes) else "warning" if changes else "compatible"
    return ContractDiffReport(
        baseline_hash=baseline.source_hash,
        runtime_hash=runtime.source_hash,
        status=status,
        decision="stop" if status == "breaking" else "continue",
        changes=changes,
    )


def _change(severity: str, kind: str, operation_id: str, detail: str, affected: list[str]) -> ContractChange:
    return ContractChange(
        severity=severity,
        kind=kind,
        operation_id=operation_id,
        detail=detail,
        affected_case_ids=affected,
    )

