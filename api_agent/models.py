"""Versioned data contracts shared by every V1 pipeline stage."""

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
