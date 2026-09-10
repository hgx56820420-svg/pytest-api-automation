"""V1 artifact generation, contract gate and controlled test execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from api_agent.artifacts import export_schemas, read_model, write_model
from api_agent.contract import compare_contracts
from api_agent.generator import generate_pytest, review_generated_script
from api_agent.models import (
    ContractDiffReport,
    CoverageReport,
    ExecutionReport,
    NormalizedRequirement,
    RequirementReview,
    ScriptReviewReport,
    TestCaseDocument,
)
from api_agent.openapi import load_openapi, normalize_openapi
from api_agent.planner import plan_cases, review_coverage
from api_agent.reporting import build_execution_report
from api_agent.requirements import review_markdown_requirements


def generate(
    source: str,
    output_dir: Path,
    requirements_md: Path | None = None,
) -> tuple[NormalizedRequirement, TestCaseDocument, CoverageReport, ScriptReviewReport, RequirementReview | None]:
    document = load_openapi(source)
    requirement = normalize_openapi(document, source)
    requirement_review = review_markdown_requirements(requirements_md, requirement) if requirements_md else None
    cases = plan_cases(requirement, requirement_review)
    coverage = review_coverage(requirement, cases, requirement_review)
    if requirement_review and requirement_review.decision != "approved":
        coverage.decision = "needs_revision"
        coverage.issues.extend(requirement_review.issues)
    script_path = output_dir / "generated-tests" / "test_generated_api.py"
    generate_pytest(script_path)
    script_review = review_generated_script(script_path, cases)

    write_model(output_dir / "normalized-requirement.json", requirement)
    if requirement_review:
        write_model(output_dir / "requirement-review.json", requirement_review)
    write_model(output_dir / "test-cases.json", cases)
    write_model(output_dir / "coverage-report.json", coverage)
    write_model(output_dir / "script-review.json", script_review)
    export_schemas(
        output_dir / "schemas",
        [RequirementReview, NormalizedRequirement, TestCaseDocument, CoverageReport, ScriptReviewReport, ContractDiffReport, ExecutionReport],
    )
    return requirement, cases, coverage, script_review, requirement_review


def check_contract(output_dir: Path, runtime_openapi: str) -> ContractDiffReport:
    baseline = read_model(output_dir / "normalized-requirement.json", NormalizedRequirement)
    cases = read_model(output_dir / "test-cases.json", TestCaseDocument)
    runtime = normalize_openapi(load_openapi(runtime_openapi), runtime_openapi)
    report = compare_contracts(baseline, runtime, cases)
    write_model(output_dir / "contract-diff.json", report)
    return report


def run(
    output_dir: Path,
    base_url: str,
    database_url: str,
    runtime_openapi: str | None = None,
    run_id: str | None = None,
) -> tuple[ExecutionReport, int]:
    requirement = read_model(output_dir / "normalized-requirement.json", NormalizedRequirement)
    cases = read_model(output_dir / "test-cases.json", TestCaseDocument)
    coverage = read_model(output_dir / "coverage-report.json", CoverageReport)
    script_review = read_model(output_dir / "script-review.json", ScriptReviewReport)
    contract = check_contract(output_dir, runtime_openapi or f"{base_url.rstrip('/')}/openapi.json")
    if coverage.decision != "approved" or script_review.decision != "approved" or contract.decision == "stop":
        report = build_execution_report(
            run_id=run_id or f"run-{uuid.uuid4().hex[:12]}",
            base_url=base_url,
            requirement_hash=requirement.source_hash,
            contract=contract,
            cases=cases,
            evidence_dir=output_dir / "evidence" / "not-run",
        )
        write_model(output_dir / "execution-report.json", report)
        return report, 3

    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    evidence_dir = output_dir / "evidence" / run_id
    allure_results = output_dir / "allure-results"
    if allure_results.exists():
        shutil.rmtree(allure_results)
    environment = os.environ.copy()
    environment.update(
        {
            "API_AGENT_BASE_URL": base_url,
            "API_AGENT_DATABASE_URL": database_url,
            "API_AGENT_EVIDENCE_DIR": str(evidence_dir.resolve()),
            "API_AGENT_RUN_ID": run_id,
            "API_AGENT_CASES_PATH": str((output_dir / "test-cases.json").resolve()),
            "API_AGENT_REQUIREMENT_PATH": str((output_dir / "normalized-requirement.json").resolve()),
        }
    )
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(output_dir / "generated-tests" / "test_generated_api.py"),
        "-q",
        "--junitxml",
        str(output_dir / "junit.xml"),
        "--alluredir",
        str(allure_results),
    ]
    completed = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=180, check=False)
    (output_dir / "pytest.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (output_dir / "pytest.stderr.log").write_text(completed.stderr, encoding="utf-8")
    report = build_execution_report(run_id, base_url, requirement.source_hash, contract, cases, evidence_dir)
    write_model(output_dir / "execution-report.json", report)
    return report, completed.returncode
