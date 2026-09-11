"""V1/V2 deterministic case planner and coverage reviewer (adapter-driven)."""

from __future__ import annotations

from api_agent.adapters import DEFAULT_ADAPTER, get_adapter
from api_agent.models import (
    CoverageItem,
    CoverageReport,
    NormalizedRequirement,
    TestCase,
    TestCaseDocument,
    RequirementReview,
)


def plan_cases(
    requirement: NormalizedRequirement,
    review: RequirementReview | None = None,
    adapter_name: str | None = None,
) -> TestCaseDocument:
    """Plan main + extra cases for one adapter's operation scenarios."""
    adapter = get_adapter(adapter_name or DEFAULT_ADAPTER)
    cases: list[TestCase] = []
    operation_ids = {operation.operation_id for operation in requirement.operations}
    for operation in requirement.operations:
        scenario = adapter.scenarios.get(operation.operation_id)
        if scenario is None:
            continue
        scenario_name, category, assertions, evidence = scenario
        documented_success = [int(code) for code in operation.responses if code.isdigit() and 200 <= int(code) < 300]
        source_refs = [operation.source_ref]
        if review and operation.operation_id in review.operation_mapping:
            source_refs.append(f"requirement:{review.operation_mapping[operation.operation_id]}")
        cases.append(
            TestCase(
                case_id=f"operation.{operation.operation_id}",
                operation_id=operation.operation_id,
                title=operation.summary or operation.operation_id,
                category=category,
                scenario=scenario_name,
                source_refs=source_refs,
                expected_status_codes=documented_success or [200],
                required_assertions=assertions,
                evidence_requirements=evidence,
            )
        )
    for template in adapter.extra_cases:
        if template.operation_id not in operation_ids:
            continue
        case = template.model_copy(deep=True)
        if review and case.operation_id in review.operation_mapping:
            case.source_refs.append(f"requirement:{review.operation_mapping[case.operation_id]}")
        cases.append(case)
    return TestCaseDocument(requirement_hash=requirement.source_hash, cases=cases)


def review_coverage(
    requirement: NormalizedRequirement,
    document: TestCaseDocument,
    requirement_review: RequirementReview | None = None,
    adapter_name: str | None = None,
) -> CoverageReport:
    adapter = get_adapter(adapter_name or DEFAULT_ADAPTER)
    items: list[CoverageItem] = []
    issues: list[str] = []
    by_operation: dict[str, list[TestCase]] = {}
    for case in document.cases:
        by_operation.setdefault(case.operation_id, []).append(case)

    for operation in requirement.operations:
        cases = by_operation.get(operation.operation_id, [])
        if not cases:
            status = "unsupported" if operation.operation_id not in adapter.scenarios else "missing"
            issues.append(f"No executable case for {operation.operation_id}")
            items.append(CoverageItem(operation_id=operation.operation_id, case_ids=[], status=status))
            continue
        missing = [
            assertion
            for assertion in ("http_status",)
            if not any(assertion in case.required_assertions for case in cases)
        ]
        # 审核前置条件：用例声明的场景必须在执行器上有对应实现，
        # 否则覆盖审核放行后，问题会拖到真实执行阶段才以失败形式暴露。
        missing_handlers = sorted(
            {
                case.scenario
                for case in cases
                if not hasattr(adapter.executor_class, f"_scenario_{case.scenario}")
            }
        )
        if missing_handlers:
            issues.append(
                f"{operation.operation_id} has no executor scenario handler: {', '.join(missing_handlers)}"
            )
        items.append(
            CoverageItem(
                operation_id=operation.operation_id,
                case_ids=[case.case_id for case in cases],
                status="covered" if not missing and not missing_handlers else "missing",
                missing_assertions=missing,
            )
        )
        if missing:
            issues.append(f"{operation.operation_id} is missing assertions: {', '.join(missing)}")

    if requirement_review and requirement_review.detected_scenarios:
        generated_scenarios = {case.scenario for case in document.cases}
        missing_scenarios = sorted(set(requirement_review.detected_scenarios) - generated_scenarios)
        for scenario in missing_scenarios:
            issues.append(f"Requirement scenario is not generated: {scenario}")

    covered = sum(item.status == "covered" for item in items)
    business_assertions = sum(
        len(case.required_assertions) for case in document.cases if case.category in {"business", "negative"}
    )
    return CoverageReport(
        decision="approved" if covered == len(requirement.operations) and not issues else "needs_revision",
        operations_total=len(requirement.operations),
        operations_covered=covered,
        cases_total=len(document.cases),
        business_assertions_total=business_assertions,
        items=items,
        issues=issues,
    )
