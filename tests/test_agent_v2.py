"""V2 multi-agent workflow tests: routing, bounded repair, correlation."""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path

import pytest

from app.main import app
from api_agent import workflow as workflow_module
from api_agent.agentlog import AgentLogger
from api_agent.artifacts import read_model, safe_name, write_model
from api_agent.models import (
    AssertionResult,
    CaseEvidence,
    ExecutionReport,
    ExecutionSummary,
    NormalizedRequirement,
    TestCaseDocument,
)
from api_agent.openapi import normalize_openapi
from api_agent.planner import plan_cases
from api_agent.repair import RepairManager
from api_agent.requirement_parser import parse_requirements
from api_agent.result_review import review_execution_results
from api_agent.workflow import V2Workflow

REQUIREMENTS_MD = Path("docs/MINI_SHOP_API_REQUIREMENTS.md")


def requirement() -> NormalizedRequirement:
    """Build the normalized Mini Shop requirement from the in-memory app."""
    return normalize_openapi(app.openapi(), "in-memory")


def _passed_evidence(run_id: str, case, request_id: str) -> CaseEvidence:
    """Craft one passed evidence record (unused helper kept for clarity)."""
    return CaseEvidence(
        run_id=run_id,
        case_id=case.case_id,
        operation_id=case.operation_id,
        status="passed",
        request_id=request_id,
        assertions=[AssertionResult(name="http_status", status="passed")],
        started_at="2026-09-10T00:00:00Z",
        duration_ms=1,
    )


def make_stub_run(*, failing_case_ids: list[str] | None = None):
    """Build a deterministic in-process replacement for pipeline.run."""

    def stub_run(output_dir, base_url, database_url, runtime_openapi=None, run_id=None):
        """Write full evidence + a PASS/FAIL execution report without HTTP."""
        requirement_model = read_model(output_dir / "normalized-requirement.json", NormalizedRequirement)
        cases = read_model(output_dir / "test-cases.json", TestCaseDocument)
        evidence_dir = output_dir / "evidence" / run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        logger = AgentLogger(evidence_dir / "agent-log.jsonl", run_id)
        results = []
        for case in cases.cases:
            request_id = f"{run_id}-{uuid.uuid4().hex[:8]}"
            failed = case.case_id in (failing_case_ids or [])
            logger.log(
                "http_request",
                request_id=request_id,
                detail={"status_code": 500 if failed else 200},
            )
            logger.log(
                "case_evidence",
                case_id=case.case_id,
                operation_id=case.operation_id,
                request_id=request_id,
            )
            evidence = CaseEvidence(
                run_id=run_id,
                case_id=case.case_id,
                operation_id=case.operation_id,
                status="failed" if failed else "passed",
                request_id=request_id,
                assertions=[
                    AssertionResult(name="http_status", status="failed" if failed else "passed")
                ],
                started_at="2026-09-10T00:00:00Z",
                duration_ms=1,
            )
            write_model(evidence_dir / f"{safe_name(case.case_id)}.json", evidence)
            results.append(evidence)
        failed_count = sum(item.status == "failed" for item in results)
        report = ExecutionReport(
            run_id=run_id,
            decision="FAIL" if failed_count else "PASS",
            target_base_url=base_url,
            requirement_hash=requirement_model.source_hash,
            contract_status="compatible",
            summary=ExecutionSummary(
                total=len(results),
                passed=len(results) - failed_count,
                failed=failed_count,
                inconclusive=0,
            ),
            cases=results,
        )
        write_model(output_dir / "execution-report.json", report)
        return report, 1 if failed_count else 0

    return stub_run


def make_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    runtime_document=None,
    max_repair_attempts: int = 2,
    failing_case_ids: list[str] | None = None,
) -> V2Workflow:
    """Build a V2Workflow with in-process OpenAPI loading and stub executor."""
    openapi_path = tmp_path / "openapi.json"
    openapi_path.write_text(json.dumps(app.openapi()), encoding="utf-8")
    runtime_path = tmp_path / "runtime"
    runtime_path.write_text(json.dumps(runtime_document or app.openapi()), encoding="utf-8")

    def stub_load_openapi(source, timeout=10.0):
        """Serve baseline/runtime OpenAPI from temp files without HTTP."""
        if str(source) == str(runtime_path):
            return json.loads(runtime_path.read_text(encoding="utf-8"))
        return json.loads(openapi_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(workflow_module, "load_openapi", stub_load_openapi)
    monkeypatch.setattr(workflow_module, "run_pipeline", make_stub_run(failing_case_ids=failing_case_ids))

    return V2Workflow(
        output_dir=tmp_path / "artifacts",
        openapi_source=str(openapi_path),
        requirements_md=REQUIREMENTS_MD,
        base_url="http://127.0.0.1:8010",
        database_url="sqlite:///./agent-test.db",
        runtime_openapi=str(runtime_path),
        max_repair_attempts=max_repair_attempts,
        run_id=f"run-{uuid.uuid4().hex[:12]}",
    )


# ---------------------------------------------------------------------------
# Requirement Parser Agent
# ---------------------------------------------------------------------------


def test_requirement_parser_extracts_interfaces_rules_and_auth():
    """The parser must extract all 22 interfaces with auth and status codes."""
    parsed = parse_requirements(REQUIREMENTS_MD)

    assert len(parsed.interfaces) == 22
    assert not parsed.issues
    assert parsed.business_rules, "core business rules should be detected"
    assert "pay_twice" in parsed.scenarios
    orders = [item for item in parsed.interfaces if item.path.startswith("/api/orders")]
    assert orders and all(item.auth_required for item in orders)
    carts = [item for item in parsed.interfaces if item.path.startswith("/api/cart")]
    assert carts and all(item.auth_required for item in carts)
    health = next(item for item in parsed.interfaces if item.path == "/health")
    assert not health.auth_required
    create_order = next(
        item for item in parsed.interfaces if item.path == "/api/orders" and item.method == "POST"
    )
    assert create_order.expected_status_codes == [201]


def test_requirement_parser_reports_missing_headings(tmp_path: Path):
    """A document without REQ headings must surface a parse issue."""
    document = tmp_path / "empty.md"
    document.write_text("# 需求\n", encoding="utf-8")

    parsed = parse_requirements(document)

    assert parsed.interfaces == []
    assert parsed.issues


# ---------------------------------------------------------------------------
# Result Review Agent
# ---------------------------------------------------------------------------


def _execution_report(tmp_path: Path, *, with_request_id: bool = True, failing: bool = False):
    """Build a full execution report with matching evidence files and log."""
    requirement_model = requirement()
    document = plan_cases(requirement_model)
    evidence_dir = tmp_path / "evidence" / "run-x"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    logger = AgentLogger(evidence_dir / "agent-log.jsonl", "run-x")
    results = []
    for case in document.cases:
        request_id = f"run-x-{uuid.uuid4().hex[:8]}" if with_request_id else ""
        logger.log(
            "case_evidence",
            case_id=case.case_id,
            operation_id=case.operation_id,
            request_id=request_id,
        )
        evidence = CaseEvidence(
            run_id="run-x",
            case_id=case.case_id,
            operation_id=case.operation_id,
            status="failed" if failing else "passed",
            request_id=request_id,
            assertions=[
                AssertionResult(name="http_status", status="failed" if failing else "passed")
            ],
            started_at="2026-09-10T00:00:00Z",
            duration_ms=1,
        )
        write_model(evidence_dir / f"{safe_name(case.case_id)}.json", evidence)
        results.append(evidence)
    report = ExecutionReport(
        run_id="run-x",
        decision="FAIL" if failing else "PASS",
        target_base_url="http://127.0.0.1:8010",
        requirement_hash=requirement_model.source_hash,
        contract_status="compatible",
        summary=ExecutionSummary(
            total=len(results),
            passed=len(results) - int(failing),
            failed=int(failing),
            inconclusive=0,
        ),
        cases=results,
    )
    return report, evidence_dir, logger


def test_result_reviewer_approves_correlated_evidence(tmp_path: Path):
    """Fully correlated passing evidence must be approved."""
    report, evidence_dir, logger = _execution_report(tmp_path)

    review = review_execution_results(report, evidence_dir, logger)

    assert review.decision == "approved"
    assert review.passed == report.summary.total
    assert not review.issues


def test_result_reviewer_rejects_uncorrelated_evidence(tmp_path: Path):
    """Evidence without request_id correlation must never be approved."""
    report, evidence_dir, logger = _execution_report(tmp_path, with_request_id=False)

    review = review_execution_results(report, evidence_dir, logger)

    assert review.decision == "needs_repair"
    assert any("request_id" in issue for issue in review.issues)


def test_result_reviewer_flags_failures_even_with_passing_report(tmp_path: Path):
    """A lying PASS execution report must not be trusted."""
    report, evidence_dir, logger = _execution_report(tmp_path, failing=True)
    report.decision = "PASS"

    review = review_execution_results(report, evidence_dir, logger)

    assert review.decision == "needs_repair"
    assert review.failed >= 1
    assert any("failed assertions" in issue for issue in review.issues)


# ---------------------------------------------------------------------------
# RepairManager
# ---------------------------------------------------------------------------


def test_repair_manager_is_bounded_and_archives_diffs(tmp_path: Path):
    """Repairs are bounded and every attempt archives a unified diff."""
    manager = RepairManager(tmp_path, max_repair_attempts=2)
    assert not manager.exhausted

    first = manager.record(
        trigger="coverage_review_failed",
        target_step="plan_cases",
        before={"cases": [1, 2]},
        after={"cases": [1, 2, 3]},
    )
    second = manager.record(
        trigger="script_review_failed",
        target_step="generate_script",
        before={"script": "a"},
        after={"script": "b"},
    )

    assert manager.exhausted
    assert first.attempt == 1 and second.attempt == 2
    assert '"script"' in second.diff
    assert '-  "script": "a"' in second.diff
    assert '+  "script": "b"' in second.diff
    assert "cases" in first.diff

    restored = RepairManager(tmp_path, max_repair_attempts=2)
    assert restored.history.escalated_to_human is False
    persisted = json.loads((tmp_path / "repair-history.json").read_text(encoding="utf-8"))
    assert len(persisted["repairs"]) == 2


def test_repair_manager_escalation_is_persisted(tmp_path: Path):
    """Escalation must flip the human-review flag and leave a record."""
    manager = RepairManager(tmp_path, max_repair_attempts=2)

    manager.escalate("no progress")

    assert manager.history.escalated_to_human
    assert manager.history.repairs[-1].trigger == "no progress"


# ---------------------------------------------------------------------------
# Full workflow (LangGraph) with in-process stubs
# ---------------------------------------------------------------------------


def test_workflow_passes_end_to_end_with_agent_messages(tmp_path: Path, monkeypatch):
    """A healthy run must walk every agent and finish PASS without repairs."""
    workflow = make_workflow(tmp_path, monkeypatch)
    result = workflow.invoke()

    assert result["final_decision"] == "PASS"
    assert workflow.repair.attempts == 0
    report = json.loads((tmp_path / "artifacts" / "workflow-report.json").read_text(encoding="utf-8"))
    assert report["decision"] == "PASS"
    step_names = [step["step"] for step in report["steps"]]
    assert step_names == [
        "parse_requirement",
        "review_requirement",
        "case_designer",
        "review_coverage",
        "script_generator",
        "review_script",
        "contract_gate",
        "executor",
        "result_reviewer",
        "finalize",
    ]
    messages = result["messages"]
    assert messages, "agents must exchange validated messages"
    senders = {message["from_agent"] for message in messages}
    assert {"requirement_parser", "coverage_reviewer", "script_reviewer", "result_reviewer"} <= senders
    assert all(message["payload_hash"] for message in messages if message["payload_ref"])


def test_workflow_log_correlates_run_case_and_request(tmp_path: Path, monkeypatch):
    """Agent log records must carry run_id and case/request correlation."""
    workflow = make_workflow(tmp_path, monkeypatch)
    workflow.invoke()

    log_path = tmp_path / "artifacts" / "evidence" / workflow.run_id / "agent-log.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    events = {record["event"] for record in records}
    assert {"step", "message", "http_request", "case_evidence"} <= events
    assert all(record["run_id"] == workflow.run_id for record in records)
    case_records = [record for record in records if record["event"] == "case_evidence"]
    assert case_records and all(record["request_id"] for record in case_records)

    logger = AgentLogger(log_path, workflow.run_id)
    assert logger.requests_for_case(case_records[0]["case_id"])


def test_workflow_selectively_regenerates_affected_cases_on_breaking_contract(
    tmp_path: Path, monkeypatch
):
    """A breaking contract change regenerates only the drifted operations."""
    drifted = deepcopy(app.openapi())
    del drifted["paths"]["/api/inventory/{product_id}/transactions"]

    workflow = make_workflow(tmp_path, monkeypatch, runtime_document=drifted)
    result = workflow.invoke()

    assert result["final_decision"] == "PASS"
    assert workflow.repair.history.repairs[0].trigger == "contract_breaking_change"
    repair = workflow.repair.history.repairs[0]
    assert repair.affected_case_ids, "removed operation must map to affected cases"
    merged = read_model(tmp_path / "artifacts" / "test-cases.json", TestCaseDocument)
    removed_operation_id = "transactions_api_inventory__product_id__transactions_get"
    assert all(case.operation_id != removed_operation_id for case in merged.cases)
    assert repair.diff, "regeneration must archive a diff"


def test_workflow_escalates_when_result_review_makes_no_progress(tmp_path: Path, monkeypatch):
    """A persistent failure must escalate after one bounded, fruitless rerun."""
    failing_case = "operation.create_order_api_orders_post"
    workflow = make_workflow(tmp_path, monkeypatch, failing_case_ids=[failing_case])
    result = workflow.invoke()

    assert result["final_decision"] == "FAIL"
    assert workflow.repair.history.escalated_to_human
    assert (tmp_path / "artifacts" / "failure-scene.json").exists()
    scene = json.loads((tmp_path / "artifacts" / "failure-scene.json").read_text(encoding="utf-8"))
    assert scene["failed_cases"]
    assert scene["agent_log"]
    reruns = [step for step in workflow.repair.history.repairs if step.trigger == "result_review_failed"]
    assert len(reruns) == 1


def test_workflow_requires_human_when_reviews_never_pass(tmp_path: Path, monkeypatch):
    """Reviews that can never pass must end at NEEDS_HUMAN, not loop forever."""
    openapi_path = tmp_path / "openapi.json"
    openapi_path.write_text(json.dumps(app.openapi()), encoding="utf-8")
    monkeypatch.setattr(
        workflow_module,
        "load_openapi",
        lambda source, timeout=10.0: json.loads(openapi_path.read_text(encoding="utf-8")),
    )
    monkeypatch.setattr(workflow_module, "run_pipeline", make_stub_run())
    workflow = V2Workflow(
        output_dir=tmp_path / "artifacts",
        openapi_source=str(openapi_path),
        requirements_md=tmp_path / "bad-requirements.md",
        base_url="http://127.0.0.1:8010",
        database_url="sqlite:///./agent-test.db",
        max_repair_attempts=2,
        run_id=f"run-{uuid.uuid4().hex[:12]}",
    )
    (tmp_path / "bad-requirements.md").write_text("#### REQ-META-001 `GET /health`\n", encoding="utf-8")

    result = workflow.invoke()

    assert result["final_decision"] == "NEEDS_HUMAN"
    assert workflow.repair.history.escalated_to_human
    report = json.loads((tmp_path / "artifacts" / "workflow-report.json").read_text(encoding="utf-8"))
    assert report["decision"] == "NEEDS_HUMAN"
