"""Result Review Agent (V2): audit execution evidence before any verdict.

The reviewer separates business verdicts from trust: a case only counts as
passed when its evidence file exists, carries a request_id correlated to the
structured log, and no assertion failed. Missing evidence stays INCONCLUSIVE
and is never upgraded to success.
"""

from __future__ import annotations

from pathlib import Path

from api_agent.agentlog import AgentLogger
from api_agent.models import CaseVerdict, ExecutionReport, ResultReviewReport


def review_execution_results(
    report: ExecutionReport,
    evidence_dir: Path,
    logger: AgentLogger,
) -> ResultReviewReport:
    verdicts: list[CaseVerdict] = []
    issues: list[str] = []

    for case in report.cases:
        case_issues: list[str] = []
        evidence_path = evidence_dir / f"{_safe_name(case.case_id)}.json"
        evidence_present = evidence_path.exists()
        if not evidence_present:
            case_issues.append("evidence file is missing")

        request_id = case.request_id
        correlated = bool(request_id) and request_id in logger.requests_for_case(case.case_id)
        if evidence_present and not request_id:
            case_issues.append("evidence has no request_id")
        elif evidence_present and not correlated:
            case_issues.append("request_id is not correlated in the agent log")

        failed_assertions = [item.name for item in case.assertions if item.status == "failed"]
        if failed_assertions:
            case_issues.append("failed assertions: " + ", ".join(failed_assertions))
        if case.status == "inconclusive" and not case_issues:
            case_issues.append("case is inconclusive without a recorded cause")

        for issue in case_issues:
            issues.append(f"{case.case_id}: {issue}")
        verdicts.append(
            CaseVerdict(
                case_id=case.case_id,
                operation_id=case.operation_id,
                status=case.status,
                evidence_present=evidence_present,
                request_id_correlated=correlated,
                issues=case_issues,
            )
        )

    if report.decision == "PASS" and issues:
        # A PASS verdict with audit issues is not trustworthy: force a repair loop.
        decision = "needs_repair"
        issues.append("Execution report says PASS but the result review found audit issues")
    elif report.decision == "FAIL":
        decision = "needs_repair"
    elif report.decision == "INCONCLUSIVE":
        decision = "needs_repair"
    else:
        decision = "approved"

    return ResultReviewReport(
        run_id=report.run_id,
        decision=decision,
        cases_total=len(verdicts),
        passed=sum(item.status == "passed" and item.evidence_present and item.request_id_correlated for item in verdicts),
        failed=sum(item.status == "failed" for item in verdicts),
        inconclusive=sum(item.status == "inconclusive" for item in verdicts),
        verdicts=verdicts,
        issues=issues,
    )


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)
