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


def compile_llm_cases(rule_set) -> list[TestCase]:
    """Compile validated LLM rules into TestCases (scenario=llm).

    断言名 = db:{kind}:{table}:{field}，与 DSL 执行器产出的断言名一致，
    使 required_assertions 闭环可校验。
    """
    cases: list[TestCase] = []
    for rule in rule_set.rules:
        assertions = ["http_status"] + [
            f"db:{item.kind}:{item.table}:{item.field or 'count'}" for item in rule.db_assertions
        ]
        cases.append(
            TestCase(
                case_id=f"llm.{rule.rule_id}",
                operation_id=f"llm.{rule.rule_id}",
                title=rule.title,
                category="business",
                scenario="llm",
                source_refs=[f"llm:{rule.rule_id}", f"doc:{rule.action}"],
                expected_status_codes=rule.expected_status_codes,
                required_assertions=assertions,
                evidence_requirements=["http", "database"],
            )
        )
    return cases


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
    # 通用契约冒烟：adapter 未预写场景的接口也要有用例，新接口不允许裸奔
    for operation in requirement.operations:
        if operation.operation_id in adapter.scenarios:
            continue
        cases.append(_generic_case(operation))
    return TestCaseDocument(requirement_hash=requirement.source_hash, cases=cases)


def _generic_case(operation) -> TestCase:
    """Build a schema-driven contract smoke case for an unmapped operation.

    期望状态码取接口文档声明的全部状态码：冒烟的价值在于抓 5xx 与
    未声明的响应，而 4xx（数据依赖导致）由响应结构断言与业务用例兜底。
    """
    documented = [int(code) for code in operation.responses if code.isdigit()]
    return TestCase(
        case_id=f"generic.{operation.operation_id}",
        operation_id=operation.operation_id,
        title=f"契约冒烟: {operation.method} {operation.path}",
        category="contract",
        scenario="generic",
        source_refs=[operation.source_ref, "generic:openapi"],
        expected_status_codes=documented or [200],
        required_assertions=["http_status", "response_schema"],
        evidence_requirements=["http"],
    )


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
        known_scenarios = {values[0] for values in adapter.scenarios.values()} | {
            case.scenario for case in adapter.extra_cases
        }
        generated_scenarios = {case.scenario for case in document.cases}
        # 只对 adapter 声明过能力的场景报警；文档里的跨领域措辞噪音不拦截流程
        missing_scenarios = sorted(
            (set(requirement_review.detected_scenarios) - generated_scenarios) & known_scenarios
        )
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
