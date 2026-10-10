"""Deterministic business workflows used by the LangGraph coordinator.

These classes own stable input/output steps.  LangGraph remains responsible
for routing, persistence and human interrupts; it no longer needs to know the
implementation details of every deterministic operation.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Any

from api_agent.artifacts import write_model
from api_agent.contract import compare_contracts
from api_agent.generator import generate_pytest, review_generated_script
from api_agent.models import (
    ContractDiffReport,
    CoverageReport,
    ExecutionReport,
    NormalizedRequirement,
    ParsedRequirement,
    RequirementReview,
    ResultReviewReport,
    ScriptReviewReport,
    TestCaseDocument,
)
from api_agent.openapi import load_openapi, normalize_openapi
from api_agent.planner import plan_cases, review_coverage
from api_agent.pipeline import run as run_pipeline
from api_agent.requirement_parser import parse_requirements
from api_agent.requirements import review_markdown_requirements
from api_agent.result_review import review_execution_results


@dataclass(frozen=True)
class RequirementWorkflow:
    """Prepare requirement and contract artifacts in parallel."""

    requirements_md: Path
    openapi_source: str
    adapter: Any
    workers: int = 2
    load_openapi_fn: Any = load_openapi

    def prepare(self, output_dir: Path) -> tuple[ParsedRequirement, NormalizedRequirement]:
        def parse_markdown() -> ParsedRequirement:
            parsed = parse_requirements(
                self.requirements_md, blanket_auth=self.adapter.blanket_auth
            )
            write_model(output_dir / "parsed-requirement.json", parsed)
            return parsed

        def normalize_contract() -> NormalizedRequirement:
            document = self.load_openapi_fn(self.openapi_source)
            requirement = normalize_openapi(document, self.openapi_source)
            write_model(output_dir / "normalized-requirement.json", requirement)
            return requirement

        with ThreadPoolExecutor(max_workers=max(1, min(self.workers, 2))) as pool:
            parsed_future = pool.submit(parse_markdown)
            requirement_future = pool.submit(normalize_contract)
            return parsed_future.result(), requirement_future.result()

    def review(self, requirement: NormalizedRequirement) -> RequirementReview:
        return review_markdown_requirements(self.requirements_md, requirement)


@dataclass(frozen=True)
class CaseWorkflow:
    adapter: str

    def design(
        self,
        requirement: NormalizedRequirement,
        review: RequirementReview,
        output_dir: Path,
        *,
        include_llm_rules: bool = True,
    ) -> TestCaseDocument:
        cases = plan_cases(requirement, review, self.adapter)
        rules_path = output_dir / "llm-rules.json"
        if include_llm_rules and rules_path.exists():
            from api_agent.llm_rules import LLMRuleSet
            from api_agent.planner import compile_llm_cases

            rule_set = LLMRuleSet.model_validate_json(rules_path.read_text(encoding="utf-8"))
            cases.cases.extend(compile_llm_cases(rule_set))
        write_model(output_dir / "test-cases.json", cases)
        return cases

    def review_coverage(
        self,
        requirement: NormalizedRequirement,
        cases: TestCaseDocument,
        review: RequirementReview,
    ) -> CoverageReport:
        return review_coverage(requirement, cases, review, self.adapter)


@dataclass(frozen=True)
class ScriptWorkflow:
    def generate(self, output_dir: Path) -> Path:
        path = output_dir / "generated-tests" / "test_generated_api.py"
        generate_pytest(path)
        return path

    def review(self, path: Path, cases: TestCaseDocument) -> ScriptReviewReport:
        return review_generated_script(path, cases)


@dataclass(frozen=True)
class ContractWorkflow:
    def compare(
        self,
        baseline: NormalizedRequirement,
        runtime: NormalizedRequirement,
        cases: TestCaseDocument,
    ) -> ContractDiffReport:
        return compare_contracts(baseline, runtime, cases)


@dataclass(frozen=True)
class ExecutionWorkflow:
    def run(
        self,
        output_dir: Path,
        base_url: str,
        database_url: str,
        runtime_openapi: str | None,
        *,
        run_id: str,
        adapter: str,
        fixed_accounts: bool,
        rules_path: Path,
        parallel_workers: int = 1,
        runner: Any = run_pipeline,
    ) -> tuple[ExecutionReport, int]:
        kwargs = {
            "run_id": run_id,
            "adapter_name": adapter,
            "fixed_accounts": fixed_accounts,
            "rules_path": rules_path,
        }
        if "parallel_workers" in inspect.signature(runner).parameters:
            kwargs["parallel_workers"] = parallel_workers
        return runner(output_dir, base_url, database_url, runtime_openapi, **kwargs)


@dataclass(frozen=True)
class ResultWorkflow:
    def review(
        self, report: ExecutionReport, evidence_dir: Path, logger: Any
    ) -> ResultReviewReport:
        return review_execution_results(report, evidence_dir, logger)
