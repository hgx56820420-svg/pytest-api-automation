"""Command line entry point for the V1/V2 API testing pipelines."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from api_agent.pipeline import check_contract, generate, run
from api_agent.workflow import V2Workflow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="api-agent", description="OpenAPI-driven API testing")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Normalize OpenAPI and generate cases and pytest")
    generate_parser.add_argument("--openapi", required=True)
    generate_parser.add_argument("--requirements-md", type=Path)
    generate_parser.add_argument("--output", type=Path, default=Path("artifacts/v1"))

    check_parser = subparsers.add_parser("check", help="Compare generated baseline with runtime OpenAPI")
    check_parser.add_argument("--runtime-openapi", required=True)
    check_parser.add_argument("--output", type=Path, default=Path("artifacts/v1"))

    run_parser = subparsers.add_parser("run", help="Run generated tests after the contract gate")
    run_parser.add_argument("--base-url", required=True)
    run_parser.add_argument("--database-url", required=True)
    run_parser.add_argument("--runtime-openapi")
    run_parser.add_argument("--output", type=Path, default=Path("artifacts/v1"))

    pipeline_parser = subparsers.add_parser("pipeline", help="Generate, gate and run in one command (V1)")
    pipeline_parser.add_argument("--openapi", required=True)
    pipeline_parser.add_argument("--requirements-md", type=Path)
    pipeline_parser.add_argument("--base-url", required=True)
    pipeline_parser.add_argument("--database-url", required=True)
    pipeline_parser.add_argument("--runtime-openapi")
    pipeline_parser.add_argument("--output", type=Path, default=Path("artifacts/v1"))

    v2_parser = subparsers.add_parser("v2", help="V2 multi-agent workflow: requirements-first with review loops and bounded repair")
    v2_parser.add_argument("--openapi", required=True, help="OpenAPI contract used as the review baseline")
    v2_parser.add_argument("--requirements-md", type=Path, required=True, help="Markdown requirement document (primary source)")
    v2_parser.add_argument("--base-url", required=True)
    v2_parser.add_argument("--database-url", required=True)
    v2_parser.add_argument("--runtime-openapi")
    v2_parser.add_argument("--output", type=Path, default=Path("artifacts/v2"))
    v2_parser.add_argument("--max-repair-attempts", type=int, default=2)

    args = parser.parse_args(argv)
    if args.command == "generate":
        requirement, cases, coverage, review, requirement_review = generate(args.openapi, args.output, args.requirements_md)
        _print({"operations": len(requirement.operations), "requirements": requirement_review.decision if requirement_review else "not_provided", "cases": len(cases.cases), "coverage": coverage.decision, "script_review": review.decision})
        return 0 if coverage.decision == "approved" and review.decision == "approved" else 2
    if args.command == "check":
        report = check_contract(args.output, args.runtime_openapi)
        _print({"status": report.status, "decision": report.decision, "changes": len(report.changes)})
        return 0 if report.decision == "continue" else 3
    if args.command == "run":
        report, returncode = run(args.output, args.base_url, args.database_url, args.runtime_openapi)
        _print(_execution_summary(report))
        return returncode
    if args.command == "v2":
        return _run_v2(args)

    requirement, cases, coverage, review, requirement_review = generate(args.openapi, args.output, args.requirements_md)
    if coverage.decision != "approved" or review.decision != "approved":
        _print({"operations": len(requirement.operations), "requirements": requirement_review.decision if requirement_review else "not_provided", "cases": len(cases.cases), "coverage": coverage.decision, "script_review": review.decision})
        return 2
    report, returncode = run(args.output, args.base_url, args.database_url, args.runtime_openapi)
    _print(_execution_summary(report))
    return returncode


def _run_v2(args: argparse.Namespace) -> int:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    workflow = V2Workflow(
        output_dir=args.output,
        openapi_source=args.openapi,
        requirements_md=args.requirements_md,
        base_url=args.base_url,
        database_url=args.database_url,
        runtime_openapi=args.runtime_openapi,
        max_repair_attempts=args.max_repair_attempts,
        run_id=run_id,
    )
    result = workflow.invoke()
    report_path = args.output / "workflow-report.json"
    summary = {
        "run_id": run_id,
        "decision": result.get("final_decision", "INCONCLUSIVE"),
        "workflow_report": str(report_path),
        "repair_attempts": workflow.repair.attempts,
        "escalated_to_human": workflow.repair.history.escalated_to_human,
        "steps": [f"{step['step']}:{step['decision']}->{step['routed_to']}" for step in result.get("steps", [])],
        "messages": len(result.get("messages", [])),
    }
    _print(summary)
    decision = result.get("final_decision", "INCONCLUSIVE")
    return 0 if decision == "PASS" else 3 if decision == "NEEDS_HUMAN" else 1


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _execution_summary(report) -> dict[str, object]:
    return {
        "run_id": report.run_id,
        "decision": report.decision,
        "contract_status": report.contract_status,
        "summary": report.summary.model_dump(),
    }
