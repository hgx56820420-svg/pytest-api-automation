from copy import deepcopy
from pathlib import Path

from app.main import app
from api_agent.contract import compare_contracts
from api_agent.generator import generate_pytest, review_generated_script
from api_agent.executor import _redact
from api_agent.openapi import normalize_openapi
from api_agent.planner import plan_cases, review_coverage
from api_agent.requirements import review_markdown_requirements


def requirement():
    return normalize_openapi(app.openapi(), "in-memory")


def test_current_openapi_has_full_v1_case_coverage():
    normalized = requirement()
    cases = plan_cases(normalized)
    coverage = review_coverage(normalized, cases)

    assert len(normalized.operations) == 14
    assert len(cases.cases) == 30
    assert coverage.decision == "approved"
    assert coverage.operations_covered == coverage.operations_total


def test_order_cases_require_database_side_effect_assertions():
    cases = plan_cases(requirement()).cases
    create_order = next(case for case in cases if case.scenario == "create_order")
    failed_order = next(case for case in cases if case.scenario == "insufficient_stock")

    assert {"order_created", "inventory_decreased", "balance_decreased"} <= set(create_order.required_assertions)
    assert {"inventory_unchanged", "balance_unchanged", "order_not_created"} <= set(failed_order.required_assertions)
    assert "database" in create_order.evidence_requirements


def test_contract_diff_blocks_removed_operation():
    baseline = requirement()
    runtime = baseline.model_copy(deep=True)
    runtime.operations = runtime.operations[1:]
    cases = plan_cases(baseline)

    report = compare_contracts(baseline, runtime, cases)

    assert report.status == "breaking"
    assert report.decision == "stop"
    assert report.changes[0].kind == "operation_removed"
    assert report.changes[0].affected_case_ids


def test_contract_diff_allows_identical_contract():
    baseline = requirement()
    report = compare_contracts(baseline, baseline.model_copy(deep=True), plan_cases(baseline))

    assert report.status == "compatible"
    assert report.decision == "continue"


def test_generated_pytest_passes_static_review(tmp_path: Path):
    cases = plan_cases(requirement())
    script = tmp_path / "test_generated.py"
    generate_pytest(script)

    report = review_generated_script(script, cases)

    assert report.decision == "approved"
    assert len(report.mapped_case_ids) == len(cases.cases)


def test_sensitive_values_are_redacted_recursively():
    value = {"password": "secret", "nested": {"access_token": "jwt"}, "items": [{"token": "x"}]}

    assert _redact(value) == {
        "password": "[REDACTED]",
        "nested": {"access_token": "[REDACTED]"},
        "items": [{"token": "[REDACTED]"}],
    }


def test_mini_shop_markdown_requirements_map_to_every_operation():
    normalized = requirement()
    review = review_markdown_requirements(Path("docs/MINI_SHOP_API_REQUIREMENTS.md"), normalized)
    cases = plan_cases(normalized, review)

    assert review.decision == "approved"
    assert len(review.entries) == len(normalized.operations)
    assert len(review.operation_mapping) == len(normalized.operations)
    assert {"wrong_password", "cancel_paid", "other_user_order"} <= set(review.detected_scenarios)
    assert all(any(ref.startswith("requirement:REQ-") for ref in case.source_refs) for case in cases.cases)


def test_markdown_requirement_review_rejects_missing_operation(tmp_path: Path):
    document = tmp_path / "requirements.md"
    document.write_text("#### REQ-META-001 `GET /health`\n", encoding="utf-8")

    review = review_markdown_requirements(document, requirement())

    assert review.decision == "needs_revision"
    assert any("missing from requirements" in issue for issue in review.issues)


def test_requirement_scenario_gap_is_reported(tmp_path: Path):
    normalized = requirement()
    document = tmp_path / "requirements.md"
    document.write_text(
        "\n".join(
            f"#### REQ-{index:03d} `{operation.method} {operation.path}`"
            for index, operation in enumerate(normalized.operations, start=1)
        ),
        encoding="utf-8",
    )
    review = review_markdown_requirements(document, normalized)
    cases = plan_cases(normalized, review)
    coverage = review_coverage(normalized, cases, review)

    assert review.detected_scenarios == []
    assert coverage.decision == "approved"
