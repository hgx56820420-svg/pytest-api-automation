"""Command line entry point for the V1 API testing pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from api_agent.pipeline import check_contract, generate, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="api-agent", description="OpenAPI-driven API testing V1")
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

    pipeline_parser = subparsers.add_parser("pipeline", help="Generate, gate and run in one command")
    pipeline_parser.add_argument("--openapi", required=True)
    pipeline_parser.add_argument("--requirements-md", type=Path)
    pipeline_parser.add_argument("--base-url", required=True)
    pipeline_parser.add_argument("--database-url", required=True)
    pipeline_parser.add_argument("--runtime-openapi")
    pipeline_parser.add_argument("--output", type=Path, default=Path("artifacts/v1"))

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

    requirement, cases, coverage, review, requirement_review = generate(args.openapi, args.output, args.requirements_md)
    if coverage.decision != "approved" or review.decision != "approved":
        _print({"operations": len(requirement.operations), "requirements": requirement_review.decision if requirement_review else "not_provided", "cases": len(cases.cases), "coverage": coverage.decision, "script_review": review.decision})
        return 2
    report, returncode = run(args.output, args.base_url, args.database_url, args.runtime_openapi)
    _print(_execution_summary(report))
    return returncode


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _execution_summary(report) -> dict[str, object]:
    return {
        "run_id": report.run_id,
        "decision": report.decision,
        "contract_status": report.contract_status,
        "summary": report.summary.model_dump(),
    }
