"""Command line entry point for the V1/V2 API testing pipelines."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from api_agent.pipeline import check_contract, generate, run
from api_agent.workflow import V2Workflow, WorkflowInputMismatch


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

    v2_parser = subparsers.add_parser(
        "v2",
        help="V2 multi-agent workflow: requirements-first with review loops",
    )
    v2_parser.add_argument("--openapi", required=True, help="OpenAPI contract used as review baseline")
    v2_parser.add_argument(
        "--requirements-md", type=Path, required=True, help="Markdown requirement document (primary source)"
    )
    v2_parser.add_argument("--base-url", required=True)
    v2_parser.add_argument("--database-url", required=True)
    v2_parser.add_argument("--runtime-openapi")
    v2_parser.add_argument("--output", type=Path, default=Path("artifacts/v2"))
    v2_parser.add_argument("--max-repair-attempts", type=int, default=2)
    v2_parser.add_argument(
        "--parallel-workers",
        type=int,
        default=1,
        help="Safe execution parallelism; adapters opt in, default is serial",
    )
    v2_parser.add_argument("--adapter", default="mini_shop", help="Domain adapter: mini_shop, library or meeting")
    v2_parser.add_argument(
        "--fixed-accounts",
        action="store_true",
        help="Deterministic per-case usernames; leftover accounts are cleaned via the adapter cleanup spec",
    )
    v2_parser.add_argument(
        "--llm-analysis",
        action="store_true",
        help="Enable the requirement analyst LLM plugin (needs LLM_API_KEY/LLM_BASE_URL/LLM_MODEL in .env)",
    )
    resume_parser = subparsers.add_parser(
        "v2-resume", help="Resume a paused V2 workflow after human review"
    )
    resume_parser.add_argument("--run-id", required=True)
    resume_parser.add_argument("--output", type=Path, default=Path("artifacts/v2"))
    resume_parser.add_argument("--openapi", required=True)
    resume_parser.add_argument("--requirements-md", type=Path, required=True)
    resume_parser.add_argument("--base-url", required=True)
    resume_parser.add_argument("--database-url", required=True)
    resume_parser.add_argument("--runtime-openapi")
    resume_parser.add_argument("--max-repair-attempts", type=int, default=2)
    resume_parser.add_argument("--parallel-workers", type=int, default=1)
    resume_parser.add_argument("--adapter", default="mini_shop")
    resume_parser.add_argument("--fixed-accounts", action="store_true")
    resume_parser.add_argument("--llm-analysis", action="store_true")
    resume_parser.add_argument("--action", choices=["approve", "retry", "reject"], required=True)
    resume_parser.add_argument("--target-step", default="")
    resume_parser.add_argument("--comment", default="")

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
    if args.command == "v2-resume":
        return _resume_v2(args)

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
        adapter=args.adapter,
        fixed_accounts=args.fixed_accounts,
        llm_analysis=args.llm_analysis,
        run_id=run_id,
        parallel_workers=args.parallel_workers,
    )
    try:
        result = workflow.invoke()
    except Exception as exc:  # degraded report keeps the audit trail intact
        report = workflow.write_error_report(exc)
        error = report.issues[0] if report.issues else "workflow failed; see workflow-report.json"
        _print(
            {
                "run_id": run_id,
                "decision": "NEEDS_HUMAN",
                "workflow_report": str(args.output / "workflow-report.json"),
                "error": error,
                "issues": report.issues,
                "retry_reasons": [
                    action.reason
                    for action in workflow.repair.history.repairs
                    if action.reason
                ],
            }
        )
        workflow.close()
        return 3
    summary = _v2_summary(workflow, result, args, run_id)
    _print(summary)
    decision = result.get("final_decision", "INCONCLUSIVE")
    workflow.close()
    return 0 if decision == "PASS" else 3 if decision == "NEEDS_HUMAN" else 1


def _resume_v2(args: argparse.Namespace) -> int:
    """Resume an existing run using its persisted SQLite checkpoint."""
    workflow = V2Workflow(
        output_dir=args.output,
        openapi_source=args.openapi,
        requirements_md=args.requirements_md,
        base_url=args.base_url,
        database_url=args.database_url,
        runtime_openapi=args.runtime_openapi,
        max_repair_attempts=args.max_repair_attempts,
        adapter=args.adapter,
        fixed_accounts=args.fixed_accounts,
        llm_analysis=args.llm_analysis,
        run_id=args.run_id,
        parallel_workers=args.parallel_workers,
    )
    try:
        result = workflow.resume(
            {
                "action": args.action,
                "target_step": args.target_step,
                "comment": args.comment,
            }
        )
        decision = result.get("final_decision", "INCONCLUSIVE")
        _print(_v2_summary(workflow, result, args, args.run_id))
        return 0 if decision == "PASS" else 3 if decision == "NEEDS_HUMAN" else 1
    except Exception as exc:
        if isinstance(exc, WorkflowInputMismatch):
            _print(
                {
                    "run_id": args.run_id,
                    "status": "WAITING_HUMAN",
                    "decision": "NEEDS_HUMAN",
                    "workflow_report": str(args.output / "workflow-report.json"),
                    "error": str(exc),
                }
            )
            return 3
        report = workflow.write_error_report(exc)
        _print(
            {
                "run_id": args.run_id,
                "decision": "NEEDS_HUMAN",
                "error": report.issues[0] if report.issues else "workflow failed; see workflow-report.json",
                "issues": report.issues,
                "retry_reasons": [
                    action.reason
                    for action in workflow.repair.history.repairs
                    if action.reason
                ],
            }
        )
        return 3
    finally:
        workflow.close()


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _v2_summary(
    workflow: V2Workflow,
    result: dict[str, object],
    args: argparse.Namespace,
    run_id: str,
) -> dict[str, object]:
    """Use one summary shape for initial runs and repeated interruptions."""
    pending = workflow.pending_interrupt()
    llm_history_path = args.output / "llm-retry-history.json"
    llm_history: dict[str, object] = {}
    if llm_history_path.exists():
        try:
            candidate = json.loads(llm_history_path.read_text(encoding="utf-8"))
            if isinstance(candidate, dict) and candidate.get("run_id") == run_id:
                llm_history = candidate
        except (OSError, json.JSONDecodeError):
            llm_history = {}
    summary: dict[str, object] = {
        "run_id": run_id,
        "status": "WAITING_HUMAN" if pending is not None else result.get("workflow_status", "COMPLETED"),
        "decision": result.get("final_decision", "INCONCLUSIVE"),
        "workflow_report": str(args.output / "workflow-report.json"),
        "repair_attempts": workflow.repair.attempts,
        "retry_reasons": [
            {
                "attempt": action.attempt,
                "trigger": action.trigger,
                "target_step": action.target_step,
                "reason": action.reason,
            }
            for action in workflow.repair.history.repairs
            if action.reason
        ],
        "escalated_to_human": workflow.repair.history.escalated_to_human,
        "steps": [
            f"{step['step']}:{step['decision']}->{step['routed_to']}"
            for step in result.get("steps", [])
        ],
        "messages": len(result.get("messages", [])),
        "issues": result.get("issues", []),
        "llm_retry_status": llm_history.get("status", "not_recorded"),
        "llm_attempts": llm_history.get("attempts", []),
    }
    if pending is not None:
        summary["interrupt"] = getattr(pending, "value", pending)
        command = [
            "python -m api_agent v2-resume",
            f"--run-id {run_id}",
            f"--output {args.output}",
            f"--openapi {args.openapi}",
            f"--requirements-md {args.requirements_md}",
            f"--base-url {args.base_url}",
            f"--database-url {args.database_url}",
        ]
        if args.runtime_openapi:
            command.append(f"--runtime-openapi {args.runtime_openapi}")
        command.extend(
            [f"--adapter {args.adapter}", f"--max-repair-attempts {args.max_repair_attempts}"]
        )
        if args.parallel_workers > 1:
            command.append(f"--parallel-workers {args.parallel_workers}")
        if args.fixed_accounts:
            command.append("--fixed-accounts")
        if args.llm_analysis:
            command.append("--llm-analysis")
        command.append("--action approve|retry|reject")
        summary["resume"] = " ".join(command)
    return summary


def _execution_summary(report) -> dict[str, object]:
    return {
        "run_id": report.run_id,
        "decision": report.decision,
        "contract_status": report.contract_status,
        "summary": report.summary.model_dump(),
    }
