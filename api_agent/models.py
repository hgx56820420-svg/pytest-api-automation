"""Versioned data contracts shared by V1 and V2 pipeline stages."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ParameterSpec(StrictModel):
    name: str
    location: Literal["path", "query", "header", "cookie"]
    required: bool = False
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")


class OperationSpec(StrictModel):
    operation_id: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    security_required: bool = False
    parameters: list[ParameterSpec] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None
    responses: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source_ref: str


class NormalizedRequirement(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    title: str
    api_version: str
    openapi_version: str
    source: str
    source_hash: str
    operations: list[OperationSpec]
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RequirementEntry(StrictModel):
    requirement_id: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str
    title: str
    line: int
    operation_id: str | None = None


class RequirementReview(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    source: str
    source_hash: str
    decision: Literal["approved", "needs_revision"]
    entries: list[RequirementEntry]
    operation_mapping: dict[str, str]
    detected_business_rules: list[str] = Field(default_factory=list)
    detected_scenarios: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TestCase(StrictModel):
    case_id: str
    operation_id: str
    title: str
    category: Literal["contract", "business", "negative", "security"]
    scenario: str
    source_refs: list[str]
    expected_status_codes: list[int]
    required_assertions: list[str]
    evidence_requirements: list[Literal["http", "database", "logs"]]


class TestCaseDocument(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    requirement_hash: str
    cases: list[TestCase]


class CoverageItem(StrictModel):
    operation_id: str
    case_ids: list[str]
    status: Literal["covered", "missing", "unsupported"]
    missing_assertions: list[str] = Field(default_factory=list)


class CoverageReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    decision: Literal["approved", "needs_revision"]
    operations_total: int
    operations_covered: int
    cases_total: int
    business_assertions_total: int
    items: list[CoverageItem]
    issues: list[str] = Field(default_factory=list)


class ContractChange(StrictModel):
    severity: Literal["warning", "breaking"]
    kind: str
    operation_id: str
    detail: str
    affected_case_ids: list[str] = Field(default_factory=list)


class ContractDiffReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    baseline_hash: str
    runtime_hash: str
    status: Literal["compatible", "warning", "breaking"]
    decision: Literal["continue", "stop"]
    changes: list[ContractChange] = Field(default_factory=list)


class ScriptReviewReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    decision: Literal["approved", "needs_revision"]
    script_path: str
    mapped_case_ids: list[str]
    missing_case_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class AssertionResult(StrictModel):
    name: str
    status: Literal["passed", "failed", "inconclusive"]
    expected: Any = None
    actual: Any = None
    detail: str = ""


class CaseEvidence(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    case_id: str
    operation_id: str
    status: Literal["passed", "failed", "inconclusive"]
    request_id: str = ""
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)
    database_before: dict[str, Any] = Field(default_factory=dict)
    database_after: dict[str, Any] = Field(default_factory=dict)
    assertions: list[AssertionResult] = Field(default_factory=list)
    started_at: str
    duration_ms: int


class ExecutionSummary(StrictModel):
    total: int
    passed: int
    failed: int
    inconclusive: int


class ExecutionReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    decision: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    target_base_url: str
    requirement_hash: str
    contract_status: Literal["compatible", "warning", "breaking"]
    summary: ExecutionSummary
    cases: list[CaseEvidence]


# ---------------------------------------------------------------------------
# V2 contracts: result review, bounded repair and workflow traceability.
# ---------------------------------------------------------------------------


class CaseVerdict(StrictModel):
    """Result Review Agent audit verdict for a single case."""

    case_id: str
    operation_id: str
    status: Literal["passed", "failed", "inconclusive"]
    evidence_present: bool
    request_id_correlated: bool
    issues: list[str] = Field(default_factory=list)


class ResultReviewReport(StrictModel):
    """Outcome of the result review over one execution report."""

    schema_version: Literal["2.0"] = "2.0"
    run_id: str
    decision: Literal["approved", "needs_repair", "needs_human"]
    cases_total: int
    passed: int
    failed: int
    inconclusive: int
    verdicts: list[CaseVerdict] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class RepairAction(StrictModel):
    """One bounded auto-repair attempt with its archived diff."""

    attempt: int
    trigger: str
    target_step: str
    affected_case_ids: list[str] = Field(default_factory=list)
    before_summary: dict[str, Any] = Field(default_factory=dict)
    after_summary: dict[str, Any] = Field(default_factory=dict)
    diff: str = ""


class RepairHistory(StrictModel):
    """Bounded repair ledger persisted as repair-history.json."""

    schema_version: Literal["2.0"] = "2.0"
    max_repair_attempts: int
    repairs: list[RepairAction] = Field(default_factory=list)
    escalated_to_human: bool = False


class StepTrace(StrictModel):
    """One visited workflow node with its decision and routing."""

    step: str
    attempt: int
    decision: str
    routed_to: str
    detail: str = ""


class WorkflowRunReport(StrictModel):
    """Final workflow trace and decision, persisted as workflow-report.json."""

    schema_version: Literal["2.0"] = "2.0"
    run_id: str
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""
    decision: Literal["PASS", "FAIL", "INCONCLUSIVE", "NEEDS_HUMAN"] = "INCONCLUSIVE"
    max_repair_attempts: int
    steps: list[StepTrace] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)


class ParsedInterface(StrictModel):
    """One API interface extracted from the Markdown requirement document."""

    requirement_id: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    path: str
    title: str = ""
    line: int = 0
    auth_required: bool = False
    expected_status_codes: list[int] = Field(default_factory=list)


class ParsedRequirement(StrictModel):
    """Requirement Parser Agent output: interfaces plus detected rules."""

    schema_version: Literal["2.0"] = "2.0"
    source: str
    source_hash: str
    title: str = ""
    interfaces: list[ParsedInterface] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentMessage(StrictModel):
    """Envelope exchanged between V2 agents; payload lives in an artifact file."""

    schema_version: Literal["2.0"] = "2.0"
    run_id: str
    from_agent: str
    to_agent: str
    topic: str
    payload_ref: str = ""
    payload_hash: str = ""
    decision: str = ""
    issues: list[str] = Field(default_factory=list)


class CleanupSpec(StrictModel):
    """Declares how the deterministic cleanup tool may touch a test database.

    The tool is evidence-driven and truth-constrained: it only deletes rows
    whose username matches ``prefix``, always SELECTs before DELETE, and
    reports "not found" honestly instead of fabricating success.
    """

    users_table: str = "users"
    username_column: str = "username"
    prefix: str = "agent_"
    # (table, user_fk_column) 删除账号前必须先清掉的业务子记录
    children: list[tuple[str, str]] = Field(default_factory=list)
