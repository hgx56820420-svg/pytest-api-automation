"""Meeting adapter tests: time-conflict rules through the generic framework."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from services.meeting.main import app as meeting_app
from api_agent import workflow as workflow_module
from api_agent.adapters import get_adapter
from api_agent.artifacts import read_model, write_model
from api_agent.executor import ScenarioExecutor
from api_agent.meeting_executor import MeetingExecutor
from api_agent.meeting_observer import MeetingObserver
from api_agent.models import NormalizedRequirement, TestCaseDocument
from api_agent.openapi import normalize_openapi
from api_agent.planner import plan_cases, review_coverage
from api_agent.requirement_parser import parse_requirements
from api_agent.requirements import review_markdown_requirements

from test_agent_v2 import make_stub_run

MEETING_MD = Path("docs/MEETING_API_REQUIREMENTS.md")


def meeting_requirement() -> NormalizedRequirement:
    return normalize_openapi(meeting_app.openapi(), "in-memory")


def make_meeting_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing_case_ids: list[str] | None = None,
    requirements_md: Path = MEETING_MD,
) -> "workflow_module.V2Workflow":
    """Build a meeting-adapter workflow with in-process stubs."""
    openapi_path = tmp_path / "meeting-openapi.json"
    openapi_path.write_text(json.dumps(meeting_app.openapi()), encoding="utf-8")

    def stub_load_openapi(source, timeout=10.0):
        return json.loads(openapi_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(workflow_module, "load_openapi", stub_load_openapi)
    monkeypatch.setattr(workflow_module, "run_pipeline", make_stub_run(failing_case_ids=failing_case_ids))

    return workflow_module.V2Workflow(
        output_dir=tmp_path / "artifacts",
        openapi_source=str(openapi_path),
        requirements_md=requirements_md,
        base_url="http://127.0.0.1:8030",
        database_url="sqlite:///./meeting-agent.db",
        runtime_openapi=None,
        max_repair_attempts=2,
        adapter="meeting",
        run_id=f"run-{uuid.uuid4().hex[:12]}",
    )


def test_meeting_adapter_is_registered_with_all_pieces():
    adapter = get_adapter("meeting")

    assert adapter.name == "meeting"
    assert adapter.executor_class is MeetingExecutor
    assert adapter.observer_class is MeetingObserver
    assert len(adapter.scenarios) == 12
    assert adapter.blanket_auth


def test_meeting_executor_is_a_scenario_executor_subclass():
    assert issubclass(MeetingExecutor, ScenarioExecutor)
    assert MeetingExecutor.observer_class is MeetingObserver


def test_meeting_requirements_parse_review_and_cover():
    requirement = meeting_requirement()
    adapter = get_adapter("meeting")
    parsed = parse_requirements(MEETING_MD, blanket_auth=adapter.blanket_auth)
    review = review_markdown_requirements(MEETING_MD, requirement)
    cases = plan_cases(requirement, review, "meeting")
    coverage = review_coverage(requirement, cases, review, "meeting")

    assert len(parsed.interfaces) == 12
    assert not parsed.issues
    bookings = [item for item in parsed.interfaces if item.path.startswith("/api/bookings")]
    assert all(item.auth_required for item in bookings)
    assert review.decision == "approved"
    assert len(review.operation_mapping) == 12
    assert coverage.decision == "approved"
    assert coverage.operations_covered == coverage.operations_total == 12
    assert len(cases.cases) == 28
    # detected scenarios must all be generated, or coverage review would fail
    generated = {case.scenario for case in cases.cases}
    assert set(review.detected_scenarios) <= generated


def test_meeting_time_conflict_cases_cover_the_new_dimension():
    """The third-target dimension: overlap rejected, adjacent allowed."""
    cases = plan_cases(meeting_requirement(), None, "meeting").cases
    conflict = next(case for case in cases if case.scenario == "booking_time_conflict")
    adjacent = next(case for case in cases if case.scenario == "booking_adjacent_slot_allowed")
    cancel = next(case for case in cases if case.scenario == "cancel_booking")

    assert conflict.category == "negative"
    assert {"balance_unchanged", "booking_not_created"} <= set(conflict.required_assertions)
    assert adjacent.category == "business"
    assert {"booking_created", "balance_decreased", "booking_amount_matches"} <= set(adjacent.required_assertions)
    assert {"booking_status_cancelled", "balance_restored"} <= set(cancel.required_assertions)


def test_meeting_workflow_passes_end_to_end(tmp_path: Path, monkeypatch):
    """The whole V2 graph must drive the meeting adapter to PASS."""
    workflow = make_meeting_workflow(tmp_path, monkeypatch)
    result = workflow.invoke()

    assert result["final_decision"] == "PASS"
    assert workflow.repair.attempts == 0
    report = json.loads((tmp_path / "artifacts" / "workflow-report.json").read_text(encoding="utf-8"))
    assert report["decision"] == "PASS"
    cases = read_model(tmp_path / "artifacts" / "test-cases.json", TestCaseDocument)
    assert len(cases.cases) == 28
    parsed = json.loads((tmp_path / "artifacts" / "parsed-requirement.json").read_text(encoding="utf-8"))
    bookings = [item for item in parsed["interfaces"] if item["path"].startswith("/api/bookings")]
    assert all(item["auth_required"] for item in bookings)


def test_meeting_workflow_unknown_service_is_not_silently_tested(tmp_path: Path, monkeypatch):
    """A doc with only unknown operations must escalate, never fake a PASS."""
    unrelated_md = tmp_path / "unrelated.md"
    workflow = make_meeting_workflow(tmp_path, monkeypatch, requirements_md=unrelated_md)
    unrelated_md.write_text(
        "\n".join(f"#### REQ-XX-{index:03d} `GET /api/unknown{index}`" for index in range(1, 4)),
        encoding="utf-8",
    )
    result = workflow.invoke()

    assert result["final_decision"] == "NEEDS_HUMAN"
    assert workflow.repair.history.escalated_to_human
