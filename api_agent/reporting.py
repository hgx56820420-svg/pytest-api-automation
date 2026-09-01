"""Build the machine-readable execution report from per-case evidence."""

from __future__ import annotations

from pathlib import Path

from api_agent.artifacts import read_model
from api_agent.models import (
    AssertionResult,
    CaseEvidence,
    ContractDiffReport,
    ExecutionReport,
    ExecutionSummary,
    TestCaseDocument,
)


def build_execution_report(
    run_id: str,
    base_url: str,
    requirement_hash: str,
    contract: ContractDiffReport,
    cases: TestCaseDocument,
    evidence_dir: Path,
) -> ExecutionReport:
    evidence_by_case: dict[str, CaseEvidence] = {}
    for path in evidence_dir.glob("*.json") if evidence_dir.exists() else []:
        evidence = read_model(path, CaseEvidence)
        evidence_by_case[evidence.case_id] = evidence

    results: list[CaseEvidence] = []
    for case in cases.cases:
        evidence = evidence_by_case.get(case.case_id)
        if evidence is None:
            evidence = CaseEvidence(
                run_id=run_id,
                case_id=case.case_id,
                operation_id=case.operation_id,
                status="inconclusive",
                assertions=[AssertionResult(name="evidence_present", status="inconclusive", expected=True, actual=False, detail="pytest produced no case evidence")],
                started_at="",
                duration_ms=0,
            )
        results.append(evidence)

    passed = sum(item.status == "passed" for item in results)
    failed = sum(item.status == "failed" for item in results)
    inconclusive = sum(item.status == "inconclusive" for item in results)
    decision = "FAIL" if failed else "INCONCLUSIVE" if inconclusive or contract.status == "breaking" else "PASS"
    return ExecutionReport(
        run_id=run_id,
        decision=decision,
        target_base_url=base_url,
        requirement_hash=requirement_hash,
        contract_status=contract.status,
        summary=ExecutionSummary(total=len(results), passed=passed, failed=failed, inconclusive=inconclusive),
        cases=results,
    )

