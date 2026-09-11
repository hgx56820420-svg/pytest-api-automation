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
from api_agent.planner import plan_cases, review_coverage
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

    def stub_run(output_dir, base_url, database_url, runtime_openapi=None, run_id=None, adapter_name=None, fixed_accounts=False, rules_path=None):
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
    adapter: str = "mini_shop",
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
        adapter=adapter,
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


def test_coverage_review_rejects_case_without_executor_handler(monkeypatch):
    """用例声明的场景缺少执行器实现时，覆盖审核必须在执行前拦截。"""
    from api_agent.adapters import DomainAdapter
    from api_agent.database import DatabaseObserver
    from api_agent.executor import ScenarioExecutor

    fake = DomainAdapter(
        name="fake",
        scenarios={
            "health_health_get": ("handler_that_does_not_exist", "contract", ["http_status"], ["http"]),
        },
        extra_cases=[],
        executor_class=ScenarioExecutor,
        observer_class=DatabaseObserver,
    )
    monkeypatch.setattr("api_agent.planner.get_adapter", lambda name=None: fake)
    mini_shop_requirement = requirement()
    cases = plan_cases(mini_shop_requirement, None, "fake")
    coverage = review_coverage(mini_shop_requirement, cases, None, "fake")

    assert coverage.decision == "needs_revision"
    assert any("no executor scenario handler" in issue for issue in coverage.issues)


def test_plan_cases_generates_generic_cases_for_unmapped_operations(monkeypatch):
    """adapter 未预写场景的新接口，必须自动生成契约冒烟用例而不是裸奔。"""
    from api_agent.adapters import DomainAdapter
    from api_agent.database import DatabaseObserver
    from api_agent.executor import ScenarioExecutor

    fake = DomainAdapter(
        name="fake",
        scenarios={},
        extra_cases=[],
        executor_class=ScenarioExecutor,
        observer_class=DatabaseObserver,
    )
    monkeypatch.setattr("api_agent.planner.get_adapter", lambda name=None: fake)
    mini_shop_requirement = requirement()
    cases = plan_cases(mini_shop_requirement, None, "fake")

    assert len(cases.cases) == len(mini_shop_requirement.operations)
    assert all(case.case_id.startswith("generic.") for case in cases.cases)
    assert all(case.scenario == "generic" for case in cases.cases)
    health = next(case for case in cases.cases if case.operation_id == "health_health_get")
    assert health.expected_status_codes == [200]
    assert "response_schema" in health.required_assertions


def test_workflow_covers_new_operations_with_generic_cases(tmp_path: Path, monkeypatch):
    """新增接口出现在契约里时，整条工作流要能用通用冒烟用例跑通并 PASS。"""
    from api_agent.adapters import ADAPTERS, DomainAdapter
    from api_agent.database import DatabaseObserver
    from api_agent.executor import ScenarioExecutor

    fake = DomainAdapter(
        name="fake",
        scenarios={},
        extra_cases=[],
        executor_class=ScenarioExecutor,
        observer_class=DatabaseObserver,
    )
    monkeypatch.setitem(ADAPTERS, "fake", fake)
    workflow = make_workflow(tmp_path, monkeypatch, adapter="fake")
    result = workflow.invoke()

    assert result["final_decision"] == "PASS"
    cases = read_model(tmp_path / "artifacts" / "test-cases.json", TestCaseDocument)
    assert len(cases.cases) == 22
    assert all(case.case_id.startswith("generic.") for case in cases.cases)


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
        "analyze_requirements",
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


def test_workflow_escalates_breaking_contract_until_human_confirms(
    tmp_path: Path, monkeypatch
):
    """P0：破坏性契约变化必须 NEEDS_HUMAN，人工确认（以新契约为基线重跑）后才恢复 PASS。"""
    drifted = deepcopy(app.openapi())
    # 破坏性变化：商品列表 200 响应结构被删（response_schema_changed）
    drifted["paths"]["/api/products"]["get"]["responses"]["200"] = {"description": "OK"}

    workflow = make_workflow(tmp_path, monkeypatch, runtime_document=drifted)
    result = workflow.invoke()

    assert result["final_decision"] == "NEEDS_HUMAN"
    assert workflow.repair.history.escalated_to_human
    assert any(r.trigger == "contract_breaking_change" for r in workflow.repair.history.repairs)

    # 人工确认后：以当前契约为基线重新运行 → PASS
    confirmed = V2Workflow(
        output_dir=tmp_path / "artifacts-confirmed",
        openapi_source=str(tmp_path / "runtime"),
        requirements_md=REQUIREMENTS_MD,
        base_url="http://127.0.0.1:8010",
        database_url="sqlite:///./agent-test.db",
        runtime_openapi=str(tmp_path / "runtime"),
        max_repair_attempts=2,
        adapter="mini_shop",
        run_id=f"run-{uuid.uuid4().hex[:12]}",
    )
    result2 = confirmed.invoke()

    assert result2["final_decision"] == "PASS"
    assert not confirmed.repair.history.escalated_to_human


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


# ---------------------------------------------------------------------------
# Deterministic cleanup tool (fixed-account mode support)
# ---------------------------------------------------------------------------


def _seed_cleanup_db(tmp_path: Path) -> str:
    """Create users + orders tables with one test account and one bystander."""
    from sqlalchemy import create_engine, text

    db_path = tmp_path / "cleanup.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)"))
        connection.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER, amount REAL)"))
        connection.execute(text("INSERT INTO users (username) VALUES ('agent_fixed_1')"))
        connection.execute(text("INSERT INTO users (username) VALUES ('alice')"))
        connection.execute(text("INSERT INTO orders (user_id, amount) VALUES (1, 10.0)"))
        connection.execute(text("INSERT INTO orders (user_id, amount) VALUES (1, 20.0)"))
        connection.execute(text("INSERT INTO orders (user_id, amount) VALUES (2, 30.0)"))
    engine.dispose()
    return f"sqlite:///{db_path}"


def test_cleanup_tool_deletes_account_and_children_only(tmp_path: Path):
    from api_agent.cleanup import CleanupTool
    from api_agent.models import CleanupSpec

    spec = CleanupSpec(prefix="agent_", children=[("orders", "user_id")])
    tool = CleanupTool(_seed_cleanup_db(tmp_path), spec)

    result = tool.cleanup_username("agent_fixed_1")

    assert result == {
        "username": "agent_fixed_1",
        "found": True,
        "deleted": True,
        "user_id": 1,
        "children_deleted": {"orders": 2},
    }
    assert tool.find_user_id("agent_fixed_1") is None
    assert tool.find_user_id("alice") == 2, "非测试账号绝不能被碰到"
    tool.close()


def test_cleanup_tool_reports_missing_account_honestly(tmp_path: Path):
    """真实性约束：账号不存在时必须如实上报，不得虚构删除成功。"""
    from api_agent.cleanup import CleanupTool
    from api_agent.models import CleanupSpec

    spec = CleanupSpec(prefix="agent_", children=[("orders", "user_id")])
    tool = CleanupTool(_seed_cleanup_db(tmp_path), spec)

    result = tool.cleanup_username("agent_never_registered")

    assert result["found"] is False
    assert result["deleted"] is False
    assert result["reason"] == "account_not_found"
    tool.close()


def test_cleanup_tool_prefix_guard_rejects_real_accounts(tmp_path: Path):
    """前缀护栏：非 agent_ 开头的账号一律拒绝清理。"""
    from api_agent.cleanup import CleanupTool
    from api_agent.models import CleanupSpec

    spec = CleanupSpec(prefix="agent_", children=[("orders", "user_id")])
    tool = CleanupTool(_seed_cleanup_db(tmp_path), spec)

    result = tool.cleanup_username("alice")

    assert result["deleted"] is False
    assert result["reason"] == "prefix_guard_rejected"
    assert tool.find_user_id("alice") == 2
    tool.close()


def test_fixed_mode_username_is_deterministic_and_within_limits(tmp_path: Path):
    """固定账号模式下用户名由 (用例, 序号) 决定，跨运行一致且不超过长度限制。"""
    from api_agent.cleanup import CleanupTool
    from api_agent.executor import ScenarioExecutor
    from api_agent.models import CleanupSpec

    executor = ScenarioExecutor(
        base_url="http://127.0.0.1:8010",
        database_url=f"sqlite:///{tmp_path / 'x.db'}",
        evidence_dir=tmp_path,
        run_id="run-x",
        requirement=requirement(),
        cleanup_spec=CleanupSpec(prefix="agent_"),
        fixed_accounts=True,
    )
    executor._case_key = "a_very_long_case_id_that_exceeds"
    executor._registration_index = 0

    first = executor._next_username()
    second = executor._next_username()
    # 同一用例同一序号跨运行必须得到同一个名字（固定账号的核心性质）
    executor._registration_index = 0
    repeat = executor._next_username()

    assert first == repeat
    assert first != second
    assert len(first) <= 20 and first.startswith("agent_")


# ---------------------------------------------------------------------------
# LLM rule contracts: DSL engine and compilation (no network needed)
# ---------------------------------------------------------------------------


def test_db_assertion_engine_field_delta_and_row_created(tmp_path: Path):
    from sqlalchemy import create_engine, text

    from api_agent.cleanup import CleanupTool  # noqa: F401
    from api_agent.database import DatabaseObserver
    from api_agent.db_assert import DbAssertionEngine
    from api_agent.llm_rules import LLMDBAssertion
    from api_agent.models import CleanupSpec  # noqa: F401

    db_path = tmp_path / "dsl.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, balance REAL)"))
        connection.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER)"))
        connection.execute(text("INSERT INTO users (username, balance) VALUES ('agent_a', 1000.0)"))
    engine.dispose()

    observer = DatabaseObserver(f"sqlite:///{db_path}")
    observer.allowed_tables = {"users": "id", "orders": "id"}
    resources = {"user": {"id": 1}}
    assertions = [
        LLMDBAssertion(kind="field_delta", table="users", field="balance", key_resource="user", delta=-20.0),
        LLMDBAssertion(kind="row_created", table="orders"),
        LLMDBAssertion(kind="row_count_unchanged", table="users"),
    ]
    engine_dsl = DbAssertionEngine(assertions, resources, observer)
    before = engine_dsl.capture_before()

    # 模拟被测服务的副作用：扣款 + 建订单
    with engine.begin() as connection:
        connection.execute(text("UPDATE users SET balance = balance - 20.0 WHERE id = 1"))
        connection.execute(text("INSERT INTO orders (user_id) VALUES (1)"))

    results = engine_dsl.evaluate(before)
    assert [item.status for item in results] == ["passed", "passed", "passed"]
    observer.close()


def test_db_assertion_engine_missing_resource_is_inconclusive(tmp_path: Path):
    from sqlalchemy import create_engine, text

    from api_agent.database import DatabaseObserver
    from api_agent.db_assert import DbAssertionEngine
    from api_agent.llm_rules import LLMDBAssertion

    db_path = tmp_path / "dsl2.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, balance REAL)"))
    engine.dispose()

    observer = DatabaseObserver(f"sqlite:///{db_path}")
    observer.allowed_tables = {"users": "id"}
    # 资源池里没有 product —— 真实性约束：如实报 inconclusive，不编造 ID
    engine_dsl = DbAssertionEngine(
        [LLMDBAssertion(kind="field_delta", table="users", field="balance", key_resource="product", delta=-1.0)],
        {},
        observer,
    )
    before = engine_dsl.capture_before()
    results = engine_dsl.evaluate(before)
    assert results[0].status == "inconclusive"
    observer.close()


def test_db_assertion_engine_rejects_table_outside_whitelist(tmp_path: Path):
    from sqlalchemy import create_engine, text

    from api_agent.database import DatabaseObserver
    from api_agent.db_assert import DbAssertionEngine
    from api_agent.llm_rules import LLMDBAssertion

    db_path = tmp_path / "dsl3.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE secrets (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    observer = DatabaseObserver(f"sqlite:///{db_path}")
    observer.allowed_tables = {"users": "id"}
    engine_dsl = DbAssertionEngine(
        [LLMDBAssertion(kind="row_created", table="secrets")],
        {},
        observer,
    )
    before = engine_dsl.capture_before()
    results = engine_dsl.evaluate(before)
    assert results[0].status == "inconclusive"
    assert "whitelist" in results[0].detail
    observer.close()


def test_compile_llm_cases_matches_dsl_assertion_names():
    from api_agent.llm_rules import LLMDBAssertion, LLMRule, LLMRuleSet
    from api_agent.planner import compile_llm_cases

    rule_set = LLMRuleSet(
        rules=[
            LLMRule(
                rule_id="REQ-ORDER-001.stock",
                title="下单后库存扣减",
                interface="POST /api/orders",
                action_path="/api/orders",
                action_body={"product_id": "{{product.id}}", "quantity": 2},
                expected_status_codes=[201],
                db_assertions=[
                    LLMDBAssertion(kind="field_delta", table="products", field="stock", key_resource="product", delta=-2),
                    LLMDBAssertion(kind="field_delta", table="users", field="balance", key_resource="user", delta=-20.0),
                    LLMDBAssertion(kind="row_created", table="orders"),
                ],
            )
        ]
    )
    cases = compile_llm_cases(rule_set)

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "llm.REQ-ORDER-001.stock"
    assert case.scenario == "llm"
    assert "db:field_delta:products:stock" in case.required_assertions
    assert "db:field_delta:users:balance" in case.required_assertions
    assert "db:row_created:orders:count" in case.required_assertions
