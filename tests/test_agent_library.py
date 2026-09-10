"""Library adapter tests: the framework must generalise to a second service."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from services.library.main import app as library_app
from api_agent import workflow as workflow_module
from api_agent.adapters import get_adapter
from api_agent.artifacts import read_model, write_model
from api_agent.executor import ScenarioExecutor
from api_agent.library_executor import LibraryExecutor
from api_agent.library_observer import LibraryObserver
from api_agent.models import ExecutionReport, NormalizedRequirement, TestCaseDocument
from api_agent.openapi import normalize_openapi
from api_agent.planner import plan_cases, review_coverage
from api_agent.requirement_parser import parse_requirements
from api_agent.requirements import review_markdown_requirements

from test_agent_v2 import make_stub_run

LIBRARY_MD = Path("docs/LIBRARY_API_REQUIREMENTS.md")


def library_requirement() -> NormalizedRequirement:
    """Normalized Library contract from the in-memory app."""
    return normalize_openapi(library_app.openapi(), "in-memory")


def make_library_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing_case_ids: list[str] | None = None,
    requirements_md: Path = LIBRARY_MD,
    max_repair_attempts: int = 2,
) -> "workflow_module.V2Workflow":
    """Build a library-adapter workflow with in-process stubs."""
    openapi_path = tmp_path / "library-openapi.json"
    openapi_path.write_text(json.dumps(library_app.openapi()), encoding="utf-8")

    def stub_load_openapi(source, timeout=10.0):
        return json.loads(openapi_path.read_text(encoding="utf-8"))

    monkeypatch.setattr(workflow_module, "load_openapi", stub_load_openapi)
    monkeypatch.setattr(workflow_module, "run_pipeline", make_stub_run(failing_case_ids=failing_case_ids))

    return workflow_module.V2Workflow(
        output_dir=tmp_path / "artifacts",
        openapi_source=str(openapi_path),
        requirements_md=requirements_md,
        base_url="http://127.0.0.1:8020",
        database_url="sqlite:///./library-agent.db",
        runtime_openapi=None,
        max_repair_attempts=max_repair_attempts,
        adapter="library",
        run_id=f"run-{uuid.uuid4().hex[:12]}",
    )


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


def test_library_adapter_is_registered_with_all_pieces():
    adapter = get_adapter("library")

    assert adapter.name == "library"
    assert adapter.executor_class is LibraryExecutor
    assert adapter.observer_class is LibraryObserver
    assert len(adapter.scenarios) == 12
    assert adapter.blanket_auth


def test_library_executor_inherits_local_guard():
    with pytest.raises(ValueError, match="local test target"):
        LibraryExecutor(
            base_url="http://127.0.0.1.evil.com",
            database_url="sqlite:///./x.db",
            evidence_dir=Path("."),
            run_id="run-x",
            requirement=library_requirement(),
        )


def test_library_executor_inherits_sqlite_observer_guard():
    with pytest.raises(ValueError, match="SQLite"):
        LibraryExecutor(
            base_url="http://127.0.0.1:8020",
            database_url="postgresql://attacker/x",
            evidence_dir=Path("."),
            run_id="run-x",
            requirement=library_requirement(),
        )


# ---------------------------------------------------------------------------
# Requirement parsing and case planning for the library domain
# ---------------------------------------------------------------------------


def test_library_requirements_parse_review_and_cover():
    requirement = library_requirement()
    adapter = get_adapter("library")
    parsed = parse_requirements(LIBRARY_MD, blanket_auth=adapter.blanket_auth)
    review = review_markdown_requirements(LIBRARY_MD, requirement)
    cases = plan_cases(requirement, review, "library")
    coverage = review_coverage(requirement, cases, review, "library")

    assert len(parsed.interfaces) == 12
    assert not parsed.issues
    borrows = [item for item in parsed.interfaces if item.path.startswith("/api/borrows")]
    assert all(item.auth_required for item in borrows)
    assert review.decision == "approved"
    assert len(review.operation_mapping) == 12
    assert coverage.decision == "approved"
    assert coverage.operations_covered == coverage.operations_total == 12
    assert len(cases.cases) == 26


def test_library_borrow_cases_require_side_effect_assertions():
    cases = plan_cases(library_requirement(), None, "library").cases
    create_borrow = next(case for case in cases if case.scenario == "create_borrow")
    return_borrow = next(case for case in cases if case.scenario == "return_borrow")
    insufficient = next(case for case in cases if case.scenario == "insufficient_copies")

    assert {"borrow_created", "copies_decreased", "deposit_decreased"} <= set(create_borrow.required_assertions)
    assert {"borrow_status_returned", "copies_restored", "deposit_restored"} <= set(return_borrow.required_assertions)
    assert {"copies_unchanged", "deposit_unchanged", "borrow_not_created"} <= set(insufficient.required_assertions)
    assert "database" in create_borrow.evidence_requirements


def test_unknown_adapter_is_rejected():
    with pytest.raises(ValueError, match="Unknown adapter"):
        get_adapter("does_not_exist")


# ---------------------------------------------------------------------------
# Full workflow on the library adapter (in-process stubs)
# ---------------------------------------------------------------------------


def test_library_workflow_passes_end_to_end(tmp_path: Path, monkeypatch):
    """The whole V2 graph must drive the library adapter to PASS."""
    workflow = make_library_workflow(tmp_path, monkeypatch)
    result = workflow.invoke()

    assert result["final_decision"] == "PASS"
    assert workflow.repair.attempts == 0
    report = json.loads((tmp_path / "artifacts" / "workflow-report.json").read_text(encoding="utf-8"))
    assert report["decision"] == "PASS"
    step_names = [step["step"] for step in report["steps"]]
    assert step_names[-1] == "finalize"
    cases = read_model(tmp_path / "artifacts" / "test-cases.json", TestCaseDocument)
    assert len(cases.cases) == 26
    parsed = json.loads((tmp_path / "artifacts" / "parsed-requirement.json").read_text(encoding="utf-8"))
    assert len(parsed["interfaces"]) == 12
    borrows = [item for item in parsed["interfaces"] if item["path"].startswith("/api/borrows")]
    assert all(item["auth_required"] for item in borrows)


def test_library_workflow_unknown_service_is_not_silently_tested(tmp_path: Path, monkeypatch):
    """A requirements doc with no matching operations must not fake a PASS.

    This is the "second target" safety property: when the adapter does not
    know the service (here: mini-shop-shaped doc against library adapter is
    inverted; we use a doc referencing only unknown operations), coverage
    review must stop the pipeline instead of executing blind scripts.
    """
    unrelated_md = tmp_path / "unrelated.md"
    workflow = make_library_workflow(tmp_path, monkeypatch, requirements_md=unrelated_md)
    unrelated_md.write_text(
        "\n".join(
            f"#### REQ-OTHER-{index:03d} `GET /api/unknown{index}`"
            for index in range(1, 4)
        ),
        encoding="utf-8",
    )
    result = workflow.invoke()

    assert result["final_decision"] == "NEEDS_HUMAN"
    assert workflow.repair.history.escalated_to_human
