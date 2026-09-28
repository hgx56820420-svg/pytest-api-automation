"""V2 multi-agent workflow orchestrated by LangGraph.

Agents exchange Pydantic-validated messages and pass artifacts by file
reference. Every review that fails routes back to the correct preceding
agent instead of restarting the pipeline. Auto-repair is bounded; when the
budget is exhausted or a repair makes no progress, the workflow escalates to
a human review node (NEEDS_HUMAN).

Topology lives in workflow_graph.py; checkpoint state/reducers live in
graph_state.py. The optional LLM analyst is a nested LangGraph subgraph.

    parse_requirement -> review_requirement -> analyze_requirements -> design_cases
        -> review_coverage -> generate_script -> review_script
        -> contract_gate -> execute -> review_results -> finalize
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from api_agent.graph_state import V2State, repair_attempts
from api_agent.workflow_graph import NODE_NAMES, build_workflow_graph

from api_agent.adapters import get_adapter
from api_agent.agentlog import AgentLogger
from api_agent.artifacts import read_model, write_json, write_model
from api_agent.llm_client import is_llm_enabled
from api_agent.contract import compare_contracts
from api_agent.generator import generate_pytest, review_generated_script
from api_agent.models import (
    AgentMessage,
    ContractDiffReport,
    CoverageReport,
    ExecutionReport,
    NormalizedRequirement,
    RepairAction,
    RepairHistory,
    RequirementReview,
    ResultReviewReport,
    ScriptReviewReport,
    StepTrace,
    TestCaseDocument,
    WorkflowRunReport,
)
from api_agent.openapi import canonical_hash, load_openapi, normalize_openapi
from api_agent.pipeline import run as run_pipeline
from api_agent.planner import plan_cases, review_coverage
from api_agent.repair import RepairManager, make_repair_action
from api_agent.requirement_parser import parse_requirements
from api_agent.requirements import review_markdown_requirements
from api_agent.result_review import review_execution_results

ARTIFACT_NAMES = [
    "parsed-requirement.json",
    "normalized-requirement.json",
    "requirement-review.json",
    "test-cases.json",
    "coverage-report.json",
    "script-review.json",
    "contract-diff.json",
    "execution-report.json",
    "result-review.json",
    "repair-history.json",
]


class V2Workflow:
    """Multi-agent workflow graph over the V1 deterministic tools."""

    def __init__(
        self,
        *,
        output_dir: Path,
        openapi_source: str,
        requirements_md: Path,
        base_url: str,
        database_url: str,
        runtime_openapi: str | None = None,
        max_repair_attempts: int = 2,
        adapter: str = "mini_shop",
        fixed_accounts: bool = False,
        llm_analysis: bool = False,
        run_id: str,
        checkpointer: BaseCheckpointSaver | None = None,
        interrupt_before: list[str] | None = None,
    ):
        if max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be non-negative")
        self.output_dir = output_dir
        self.openapi_source = openapi_source
        self.requirements_md = requirements_md
        self.base_url = base_url
        self.database_url = database_url
        self.runtime_openapi = runtime_openapi or f"{base_url.rstrip('/')}/openapi.json"
        self.adapter = adapter
        self.fixed_accounts = fixed_accounts
        self.llm_analysis = llm_analysis
        self.run_id = run_id
        self.max_repair_attempts = max_repair_attempts
        self.checkpointer = checkpointer if checkpointer is not None else InMemorySaver()
        self.interrupt_before = interrupt_before
        self.settings_hash = canonical_hash({
            "output_dir": str(output_dir.resolve()),
            "openapi_source": openapi_source,
            "requirements_md": str(requirements_md.resolve()),
            "base_url": base_url, "database_url": database_url,
            "runtime_openapi": self.runtime_openapi, "adapter": adapter,
            "fixed_accounts": fixed_accounts, "llm_analysis": llm_analysis,
            "max_repair_attempts": max_repair_attempts,
        })
        self.logger = AgentLogger(output_dir / "evidence" / run_id / "agent-log.jsonl", run_id)
        self.graph = self._build_graph()

    # -- graph assembly -----------------------------------------------------

    def _build_graph(self) -> Any:
        graph = build_workflow_graph({name: getattr(self, name) for name in NODE_NAMES})
        return graph.compile(
            checkpointer=self.checkpointer,
            interrupt_before=self.interrupt_before,
            name="api-agent-v2",
        )

    def _config(self, config: RunnableConfig | None = None) -> RunnableConfig:
        merged = dict(config or {})
        configurable = dict(merged.get("configurable", {}))
        if configurable.get("thread_id", self.run_id) != self.run_id:
            raise ValueError("thread_id must equal run_id (artifact/evidence identity)")
        if configurable.get("checkpoint_id") or configurable.get("checkpoint_ns"):
            raise ValueError("V2Workflow resumes the latest root checkpoint only")
        configurable["thread_id"] = self.run_id
        merged["configurable"] = configurable
        # A repair can revisit up to the full path. Budget > 2 must not hit
        # LangGraph's default recursion limit unexpectedly.
        merged.setdefault("recursion_limit", len(NODE_NAMES) * (self.max_repair_attempts + 2))
        return merged

    @property
    def repair(self) -> RepairManager:
        """Compatibility snapshot; nodes never use it as execution state."""
        state = self.graph.get_state(self._config()).values
        manager = RepairManager(self.output_dir, self.max_repair_attempts)
        manager.history = self._repair_history(state)
        return manager

    def resume(self, *, config: RunnableConfig | None = None) -> dict[str, Any]:
        """Resume the latest checkpoint with the same settings and artifacts.

        External HTTP side effects are not rolled back by a checkpoint.
        """
        config = self._config(config)
        snapshot = self.graph.get_state(config)
        if not snapshot.values:
            raise ValueError("No checkpoint exists for this run_id")
        if snapshot.values.get("settings_hash") != self.settings_hash:
            raise ValueError("Workflow settings differ from the checkpoint")
        if not snapshot.next:
            return dict(snapshot.values)
        return self.graph.invoke(None, config=config)

    def invoke(
        self,
        initial_state: V2State | None = None,
        *,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Start a new run; use resume() to continue an existing checkpoint."""
        config = self._config(config)
        if self.graph.get_state(config).values:
            raise ValueError("run_id already has a checkpoint; use resume() or a new run_id")
        state: V2State = {
            "run_id": self.run_id,
            "output_dir": str(self.output_dir),
            "settings_hash": self.settings_hash,
            "max_repair_attempts": self.max_repair_attempts,
            "steps": [], "messages": [], "issues": [],
            "prev_signatures": {}, "repair_history": [],
            "repair_escalated": False, "llm_rules": None,
        }
        if initial_state:
            for key in ("run_id", "output_dir", "settings_hash", "max_repair_attempts"):
                if key in initial_state and initial_state[key] != state[key]:
                    raise ValueError(f"initial_state cannot override {key}")
            state.update(initial_state)
        return self.graph.invoke(state, config=config)

    # -- shared helpers -----------------------------------------------------

    def _trace(
        self,
        state: V2State,
        step: str,
        decision: str,
        routed_to: str,
        detail: str = "",
    ) -> dict[str, Any]:
        self.logger.log(
            "step",
            agent=step,
            detail={"decision": decision, "routed_to": routed_to, "detail": detail},
        )
        record = StepTrace(
            step=step,
            attempt=repair_attempts(state) + 1,
            decision=decision,
            routed_to=routed_to,
            detail=detail,
        ).model_dump()
        return {"steps": [record]}

    def _message(
        self,
        state: V2State,
        from_agent: str,
        to_agent: str,
        topic: str,
        payload_ref: str = "",
        decision: str = "",
        issues: list[str] | None = None,
    ) -> dict[str, Any]:
        message = AgentMessage(
            run_id=self.run_id,
            from_agent=from_agent,
            to_agent=to_agent,
            topic=topic,
            payload_ref=payload_ref,
            payload_hash=_file_hash(payload_ref),
            decision=decision,
            issues=issues or [],
        )
        self.logger.log(
            "message",
            agent=from_agent,
            detail={"to": to_agent, "topic": topic, "payload_ref": payload_ref, "decision": decision},
        )
        return {"messages": [message.model_dump()]}

    def _repair_history(self, state: V2State) -> RepairHistory:
        return RepairHistory(
            max_repair_attempts=self.max_repair_attempts,
            repairs=state.get("repair_history", []),
            escalated_to_human=state.get("repair_escalated", False),
        )

    def _archive_repair(self, state: V2State, update: dict[str, Any]) -> dict[str, Any]:
        """Export a state projection for audit, not runtime storage."""
        projected = {
            **state, **update,
            "repair_history": state.get("repair_history", []) + update.get("repair_history", []),
        }
        write_model(self.output_dir / "repair-history.json", self._repair_history(projected))
        return update

    def _record_repair(self, state: V2State, **kwargs: Any) -> dict[str, Any]:
        action = make_repair_action(attempt=repair_attempts(state) + 1, **kwargs)
        return self._archive_repair(state, {"repair_history": [action.model_dump()]})

    def _escalate(self, state: V2State, reason: str) -> dict[str, Any]:
        action = RepairAction(
            attempt=repair_attempts(state), trigger=reason, target_step="human_review",
        )
        return self._archive_repair(state, {
            "issues": [reason], "repair_escalated": True,
            "repair_history": [action.model_dump()],
        })

    def _repair_route(
        self, state: V2State, *, loop_key: str, artifact: Path,
        trigger: str, target_step: str, signature: str | None = None,
    ) -> dict[str, Any]:
        """Domain repair policy; all decisions read checkpointed state."""
        current_signature = signature or _file_hash(artifact)
        previous = state.get("prev_signatures", {}).get(loop_key)
        if previous is not None and previous == current_signature:
            return {
                **self._escalate(state, f"{loop_key}: repair produced no progress"),
                "route": "human",
            }
        if repair_attempts(state) >= self.max_repair_attempts:
            return {
                **self._escalate(state, f"{loop_key}: max repair attempts ({self.max_repair_attempts}) reached"),
                "route": "human",
            }
        update = self._record_repair(
            state, trigger=trigger, target_step=target_step,
            before={"previous_signature": previous} if previous is not None else {},
            after=_read_json(artifact) or {},
        )
        return {
            **update, "route": "retry",
            "prev_signatures": {**state.get("prev_signatures", {}), loop_key: current_signature},
        }

    # -- agents -------------------------------------------------------------

    def parse_requirement(self, state: V2State) -> dict[str, Any]:
        """Requirement Parser Agent: Markdown interfaces + OpenAPI baseline."""
        document = load_openapi(self.openapi_source)
        requirement = normalize_openapi(document, self.openapi_source)
        write_model(self.output_dir / "normalized-requirement.json", requirement)
        parsed = parse_requirements(
            self.requirements_md, blanket_auth=get_adapter(self.adapter).blanket_auth
        )
        write_model(self.output_dir / "parsed-requirement.json", parsed)
        detail = (
            f"{len(parsed.interfaces)} interfaces from requirements, "
            f"{len(requirement.operations)} operations from OpenAPI"
        )
        update = self._trace(state, "parse_requirement", "done", "review_requirement", detail)
        update.update(
            self._message(
                state,
                "requirement_parser",
                "requirement_reviewer",
                "parsed_requirement",
                str(self.output_dir / "parsed-requirement.json"),
            )
        )
        return update

    def review_requirement(self, state: V2State) -> dict[str, Any]:
        """Requirement Review Agent: cross-check Markdown against OpenAPI."""
        requirement = read_model(self.output_dir / "normalized-requirement.json", NormalizedRequirement)
        review = review_markdown_requirements(self.requirements_md, requirement)
        write_model(self.output_dir / "requirement-review.json", review)
        approved = review.decision == "approved"
        update = self._message(
            state,
            "requirement_reviewer",
            "requirement_analyst" if approved else "requirement_parser",
            "requirement_review",
            str(self.output_dir / "requirement-review.json"),
            review.decision,
            review.issues,
        )
        if approved:
            update.update(self._trace(state, "review_requirement", "approved", "analyze_requirements"))
            update["route"] = "approved"
        else:
            route_update = self._repair_route(
                state,
                loop_key="requirement",
                artifact=self.output_dir / "requirement-review.json",
                trigger="requirement_review_failed",
                target_step="parse_requirement",
            )
            update.update(route_update)
            update.update(
                self._trace(
                    state,
                    "review_requirement",
                    review.decision,
                    route_update["route"],
                    "; ".join(review.issues[:3]),
                )
            )
        return update

    def analyze_requirements(self, state: V2State) -> dict[str, Any]:
        """LLM 需求拆解节点：显式开启且配置齐全时才调用大模型。"""
        if not (self.llm_analysis and is_llm_enabled()):
            return self._trace(
                state,
                "analyze_requirements",
                "skipped",
                "design_cases",
                "LLM analysis disabled (no --llm-analysis or missing .env config)",
            )
        try:
            from api_agent.adapters import get_adapter
            from api_agent.llm_analyst import analyze_requirements, analyst_model_name

            requirement = read_model(self.output_dir / "normalized-requirement.json", NormalizedRequirement)
            rules, repairs = analyze_requirements(
                requirement,
                self.requirements_md,
                get_adapter(self.adapter).observable_tables,
            )
            rules_doc = rules.model_dump()
            if repairs:
                rules_doc["repairs"] = repairs
            write_json(self.output_dir / "llm-rules.json", rules_doc)
            detail = f"{len(rules.rules)} rules extracted by {analyst_model_name()}"
            if repairs:
                detail += f", {len(repairs)} deterministic id repairs"
            update = self._trace(state, "analyze_requirements", "done", "design_cases", detail)
            update.update(
                self._message(
                    state,
                    "requirement_analyst",
                    "case_designer",
                    "llm_rules",
                    str(self.output_dir / "llm-rules.json"),
                )
            )
            return {**update, "llm_rules": rules_doc}
        except Exception as exc:  # LLM 失败不阻断确定性覆盖，只记录问题
            update = self._trace(state, "analyze_requirements", "llm_error", "design_cases", f"{type(exc).__name__}: {exc}")
            return {"issues": [f"llm analysis failed: {type(exc).__name__}: {exc}"], "llm_rules": None, **update}

    def design_cases(self, state: V2State) -> dict[str, Any]:
        """Case Design Agent: plan the standard case JSON from the baseline."""
        requirement = read_model(self.output_dir / "normalized-requirement.json", NormalizedRequirement)
        requirement_review = read_model(self.output_dir / "requirement-review.json", RequirementReview)
        cases = plan_cases(requirement, requirement_review, self.adapter)
        if state.get("llm_rules") is not None:
            from api_agent.llm_rules import LLMRuleSet
            from api_agent.planner import compile_llm_cases

            rule_set = LLMRuleSet.model_validate(state["llm_rules"])
            cases.cases.extend(compile_llm_cases(rule_set))
        write_model(self.output_dir / "test-cases.json", cases)
        update = self._trace(state, "case_designer", "done", "review_coverage", f"{len(cases.cases)} cases")
        update.update(
            self._message(
                state,
                "case_designer",
                "coverage_reviewer",
                "test_cases",
                str(self.output_dir / "test-cases.json"),
            )
        )
        return update

    def review_coverage(self, state: V2State) -> dict[str, Any]:
        """Coverage Review Agent: approve or send gaps back to the designer."""
        requirement = read_model(self.output_dir / "normalized-requirement.json", NormalizedRequirement)
        cases = read_model(self.output_dir / "test-cases.json", TestCaseDocument)
        requirement_review = read_model(self.output_dir / "requirement-review.json", RequirementReview)
        coverage = review_coverage(requirement, cases, requirement_review, self.adapter)
        write_model(self.output_dir / "coverage-report.json", coverage)
        approved = coverage.decision == "approved"
        update = self._message(
            state,
            "coverage_reviewer",
            "script_generator" if approved else "case_designer",
            "coverage_review",
            str(self.output_dir / "coverage-report.json"),
            coverage.decision,
            coverage.issues,
        )
        if approved:
            update.update(self._trace(state, "review_coverage", "approved", "generate_script"))
            update["route"] = "approved"
        else:
            route_update = self._repair_route(
                state,
                loop_key="coverage",
                artifact=self.output_dir / "coverage-report.json",
                trigger="coverage_review_failed",
                target_step="plan_cases",
            )
            update.update(route_update)
            update.update(
                self._trace(
                    state,
                    "review_coverage",
                    coverage.decision,
                    route_update["route"],
                    "; ".join(coverage.issues[:3]),
                )
            )
        return update

    def generate_script(self, state: V2State) -> dict[str, Any]:
        """Script Generator Agent: render the controlled pytest adapter."""
        script_path = self.output_dir / "generated-tests" / "test_generated_api.py"
        generate_pytest(script_path)
        update = self._trace(state, "script_generator", "done", "review_script")
        update.update(
            self._message(
                state,
                "script_generator",
                "script_reviewer",
                "generated_script",
                str(script_path),
            )
        )
        return update

    def review_script(self, state: V2State) -> dict[str, Any]:
        """Script Review Agent: static checks; failures go back to the generator."""
        cases = read_model(self.output_dir / "test-cases.json", TestCaseDocument)
        script_path = self.output_dir / "generated-tests" / "test_generated_api.py"
        review = review_generated_script(script_path, cases)
        write_model(self.output_dir / "script-review.json", review)
        approved = review.decision == "approved"
        update = self._message(
            state,
            "script_reviewer",
            "contract_gate" if approved else "script_generator",
            "script_review",
            str(self.output_dir / "script-review.json"),
            review.decision,
            review.issues,
        )
        if approved:
            update.update(self._trace(state, "review_script", "approved", "contract_gate"))
            update["route"] = "approved"
        else:
            route_update = self._repair_route(
                state,
                loop_key="script",
                artifact=self.output_dir / "script-review.json",
                trigger="script_review_failed",
                target_step="generate_script",
            )
            update.update(route_update)
            update.update(
                self._trace(
                    state,
                    "review_script",
                    review.decision,
                    route_update["route"],
                    "; ".join(review.issues[:3]),
                )
            )
        return update

    def contract_gate(self, state: V2State) -> dict[str, Any]:
        """Contract gate: continue, selectively regenerate, or stop for human."""
        baseline = read_model(self.output_dir / "normalized-requirement.json", NormalizedRequirement)
        cases = read_model(self.output_dir / "test-cases.json", TestCaseDocument)
        runtime = normalize_openapi(load_openapi(self.runtime_openapi), self.runtime_openapi)
        report = compare_contracts(baseline, runtime, cases)
        write_model(self.output_dir / "contract-diff.json", report)
        update = self._message(
            state,
            "contract_gate",
            "executor" if report.decision == "continue" else "case_designer",
            "contract_diff",
            str(self.output_dir / "contract-diff.json"),
            report.decision,
            [change.detail for change in report.changes],
        )
        if report.decision == "continue":
            update.update(self._trace(state, "contract_gate", report.status, "execute"))
            update["route"] = "continue"
        elif repair_attempts(state) >= self.max_repair_attempts:
            update.update(
                self._escalate(state, "contract: max repair attempts reached on breaking changes")
            )
            update.update(self._trace(state, "contract_gate", report.status, "human"))
            update["route"] = "human"
        else:
            # P0 修复：破坏性契约变化必须人工确认。选择性再生照常执行
            # （保留再生能力），但本轮升级 NEEDS_HUMAN，人工确认后以当前
            # 契约为基线重跑才会恢复 PASS——防止服务端误删接口被静默消化。
            update.update(
                self._escalate(
                    state,
                    "contract: breaking changes need human confirmation before the baseline is regenerated",
                )
            )
            update.update(
                self._trace(
                    state,
                    "contract_gate",
                    report.status,
                    "regenerate_affected",
                    f"{len(report.changes)} changes (escalated for human confirmation)",
                )
            )
            update["route"] = "regenerate"
        return update

    def regenerate_affected(self, state: V2State) -> dict[str, Any]:
        """Selective regeneration: only cases of operations that drifted."""
        runtime = normalize_openapi(load_openapi(self.runtime_openapi), self.runtime_openapi)
        contract = read_model(self.output_dir / "contract-diff.json", ContractDiffReport)
        affected_operations = {
            change.operation_id for change in contract.changes if change.severity == "breaking"
        }
        old_cases = read_model(self.output_dir / "test-cases.json", TestCaseDocument)
        before = old_cases.model_dump()

        runtime_operation_ids = {operation.operation_id for operation in runtime.operations}
        kept = [
            case
            for case in old_cases.cases
            if case.operation_id not in affected_operations
            and case.operation_id in runtime_operation_ids
        ]
        replanned = plan_cases(runtime, None, self.adapter)
        regenerated = [
            case for case in replanned.cases if case.operation_id in affected_operations
        ]
        merged = TestCaseDocument(requirement_hash=runtime.source_hash, cases=kept + regenerated)
        write_model(self.output_dir / "normalized-requirement.json", runtime)
        write_model(self.output_dir / "test-cases.json", merged)
        generate_pytest(self.output_dir / "generated-tests" / "test_generated_api.py")

        affected_case_ids = sorted(
            {case.case_id for case in old_cases.cases if case.operation_id in affected_operations}
        )
        repair_update = self._record_repair(
            state,
            trigger="contract_breaking_change",
            target_step="plan_cases",
            before=before,
            after=merged.model_dump(),
            affected_case_ids=affected_case_ids,
        )
        detail = (
            f"regenerated {len(regenerated)} cases "
            f"for {len(affected_operations)} drifted operations"
        )
        update = {**repair_update, **self._trace(state, "regenerate_affected", "done", "contract_gate", detail)}
        update.update(
            self._message(
                state,
                "contract_gate",
                "case_designer",
                "selective_regeneration",
                str(self.output_dir / "test-cases.json"),
                issues=[f"regenerated cases: {', '.join(affected_case_ids)}"],
            )
        )
        return update

    def execute(self, state: V2State) -> dict[str, Any]:
        """Executor Agent: run generated tests and collect evidence."""
        # Always refresh the execution rules from this run's checkpoint.
        # A reused artifact directory must never reactivate an older LLM run.
        write_json(self.output_dir / "llm-rules.json", state.get("llm_rules") or {"rules": []})
        report, returncode = run_pipeline(
            self.output_dir,
            self.base_url,
            self.database_url,
            self.runtime_openapi,
            run_id=self.run_id,
            adapter_name=self.adapter,
            fixed_accounts=self.fixed_accounts,
            rules_path=self.output_dir / "llm-rules.json",
        )
        summary = (
            f"returncode={returncode}, passed={report.summary.passed}, "
            f"failed={report.summary.failed}, inconclusive={report.summary.inconclusive}"
        )
        update = self._trace(state, "executor", report.decision, "review_results", summary)
        update.update(
            self._message(
                state,
                "executor",
                "result_reviewer",
                "execution_report",
                str(self.output_dir / "execution-report.json"),
                report.decision,
            )
        )
        return update

    def review_results(self, state: V2State) -> dict[str, Any]:
        """Result Review Agent: audit evidence; failures re-enter the executor."""
        report = read_model(self.output_dir / "execution-report.json", ExecutionReport)
        evidence_dir = self.output_dir / "evidence" / self.run_id
        review = review_execution_results(report, evidence_dir, self.logger)
        write_model(self.output_dir / "result-review.json", review)
        signature = f"{review.passed}/{review.failed}/{review.inconclusive}"
        approved = review.decision == "approved"
        update = self._message(
            state,
            "result_reviewer",
            "finalize" if approved else "executor",
            "result_review",
            str(self.output_dir / "result-review.json"),
            review.decision,
            review.issues[:5],
        )
        if approved:
            update.update(self._trace(state, "result_reviewer", "approved", "finalize"))
            update["route"] = "approved"
        else:
            route_update = self._repair_route(
                state,
                loop_key="results",
                artifact=self.output_dir / "result-review.json",
                trigger="result_review_failed",
                target_step="rerun_failed",
                signature=signature,
            )
            update.update(route_update)
            detail = (
                f"passed={review.passed}, failed={review.failed}, "
                f"inconclusive={review.inconclusive}"
            )
            update.update(
                self._trace(state, "result_reviewer", review.decision, route_update["route"], detail)
            )
        return update

    def finalize(self, state: V2State) -> dict[str, Any]:
        """Finalize: map review outcomes to PASS/FAIL/NEEDS_HUMAN and archive."""
        escalated = state.get("repair_escalated", False)
        write_model(self.output_dir / "repair-history.json", self._repair_history(state))
        failed_cases = 0
        approved = False
        current_review = self._current_run_result_review()
        if current_review is not None:
            failed_cases = current_review.failed
            approved = current_review.decision == "approved"

        if approved and not escalated:
            decision = "PASS"
        elif failed_cases:
            decision = "FAIL"
        else:
            decision = "NEEDS_HUMAN"

        artifacts = {
            name: str(self.output_dir / name)
            for name in ARTIFACT_NAMES
            if (self.output_dir / name).exists()
        }
        self.logger.log("step", agent="finalize", detail={"decision": decision, "routed_to": "END"})
        steps = state.get("steps", []) + [
            StepTrace(
                step="finalize",
                attempt=repair_attempts(state) + 1,
                decision=decision,
                routed_to="END",
            ).model_dump()
        ]
        workflow_report = WorkflowRunReport(
            run_id=self.run_id,
            finished_at=datetime.now(timezone.utc).isoformat(),
            decision=decision,
            max_repair_attempts=self.max_repair_attempts,
            steps=steps,
            artifacts=artifacts,
            issues=state.get("issues", []),
        )
        write_model(self.output_dir / "workflow-report.json", workflow_report)
        if decision != "PASS":
            self._preserve_failure_scene(state, decision)
        return {"steps": steps[-1:], "final_decision": decision}

    def write_error_report(self, exc: BaseException) -> WorkflowRunReport:
        """Write a degraded report when the graph itself raises.

        Without this, a crashed node would leave no workflow-report.json and
        no failure scene, breaking the human-review escalation contract.
        """
        reason = f"workflow crashed: {type(exc).__name__}: {exc}"
        self.logger.log("step", agent="workflow", level="error", detail={"error": reason})
        state = self.graph.get_state(self._config()).values
        update = self._escalate(state, reason)
        workflow_report = WorkflowRunReport(
            run_id=self.run_id,
            finished_at=datetime.now(timezone.utc).isoformat(),
            decision="NEEDS_HUMAN",
            max_repair_attempts=self.max_repair_attempts,
            steps=state.get("steps", []),
            issues=state.get("issues", []) + update["issues"],
        )
        write_model(self.output_dir / "workflow-report.json", workflow_report)
        scene = {
            "run_id": self.run_id,
            "decision": "NEEDS_HUMAN",
            "escalated_to_human": True,
            "issues": [reason],
            "failed_cases": [],
            "evidence_dir": str((self.output_dir / "evidence" / self.run_id).resolve()),
            "agent_log": str((self.output_dir / "evidence" / self.run_id / "agent-log.jsonl").resolve()),
        }
        write_json(self.output_dir / "failure-scene.json", scene)
        return workflow_report

    def _current_run_result_review(self) -> ResultReviewReport | None:
        """Read result-review.json only when it belongs to this run."""
        path = self.output_dir / "result-review.json"
        if not path.exists():
            return None
        review = read_model(path, ResultReviewReport)
        if review.run_id != self.run_id:
            # stale artifact from a previous run in the shared output directory
            return None
        return review

    def _preserve_failure_scene(self, state: V2State, decision: str) -> None:
        failed: list[dict[str, Any]] = []
        review = self._current_run_result_review()
        if review is not None:
            failed = [
                verdict.model_dump()
                for verdict in review.verdicts
                if verdict.status != "passed" or verdict.issues
            ]
        scene = {
            "run_id": self.run_id,
            "decision": decision,
            "escalated_to_human": state.get("repair_escalated", False),
            "issues": state.get("issues", []),
            "failed_cases": failed,
            "evidence_dir": str((self.output_dir / "evidence" / self.run_id).resolve()),
            "agent_log": str((self.output_dir / "evidence" / self.run_id / "agent-log.jsonl").resolve()),
        }
        write_json(self.output_dir / "failure-scene.json", scene)


def _file_hash(path: Path | str) -> str:
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return canonical_hash(file_path.read_text(encoding="utf-8"))


def _read_json(path: Path) -> dict[str, Any] | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))
