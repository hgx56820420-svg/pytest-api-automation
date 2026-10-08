"""V2 multi-agent workflow orchestrated by LangGraph.

Agents exchange Pydantic-validated messages and pass artifacts by file
reference. Every review that fails routes back to the correct preceding
agent instead of restarting the pipeline. Auto-repair is bounded; when the
budget is exhausted or a repair makes no progress, the workflow escalates to
a human review node (NEEDS_HUMAN).

Node map (all deterministic today; an LLM-backed agent can replace any node
without changing the graph):

    parse_requirement -> review_requirement -> design_cases
        -> review_coverage -> generate_script -> review_script
        -> contract_gate -> execute -> review_results -> finalize
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

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
    HumanReviewDecision,
    NormalizedRequirement,
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
from api_agent.repair import RepairManager
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
    "human-review.json",
    "workflow-checkpoints.sqlite3",
]


class V2State(TypedDict, total=False):
    """Shared LangGraph state: route drives conditional edges, the rest is trace."""

    run_id: str
    output_dir: str
    route: str
    steps: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    issues: list[str]
    prev_signatures: dict[str, str]
    pending_repairs: dict[str, dict[str, Any]]
    pending_human_review: dict[str, Any]
    human_approved: bool
    human_rejected: bool
    workflow_status: str
    final_decision: str


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
    ):
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
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.repair = RepairManager(output_dir, max_repair_attempts, run_id=run_id)
        self.logger = AgentLogger(output_dir / "evidence" / run_id / "agent-log.jsonl", run_id)
        self.checkpoint_path = output_dir / "workflow-checkpoints.sqlite3"
        self._checkpoint_connection = sqlite3.connect(
            str(self.checkpoint_path), check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        self.checkpointer.setup()
        self.graph = self._build_graph()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # -- graph assembly -----------------------------------------------------

    def _build_graph(self) -> Any:
        """Wire agent nodes with conditional review/retry/human edges."""
        graph = StateGraph(V2State)
        graph.add_node("parse_requirement", self.parse_requirement)
        graph.add_node("review_requirement", self.review_requirement)
        graph.add_node("analyze_requirements", self.analyze_requirements)
        graph.add_node("design_cases", self.design_cases)
        graph.add_node("review_coverage", self.review_coverage)
        graph.add_node("generate_script", self.generate_script)
        graph.add_node("review_script", self.review_script)
        graph.add_node("contract_gate", self.contract_gate)
        graph.add_node("regenerate_affected", self.regenerate_affected)
        graph.add_node("execute", self.execute)
        graph.add_node("review_results", self.review_results)
        graph.add_node("human_review", self.human_review)
        graph.add_node("finalize", self.finalize)

        graph.add_edge(START, "parse_requirement")
        graph.add_edge("parse_requirement", "review_requirement")
        graph.add_conditional_edges(
            "review_requirement",
            lambda state: state["route"],
            {"approved": "analyze_requirements", "retry": "parse_requirement", "human": "human_review"},
        )
        graph.add_edge("analyze_requirements", "design_cases")
        graph.add_edge("design_cases", "review_coverage")
        graph.add_conditional_edges(
            "review_coverage",
            lambda state: state["route"],
            {"approved": "generate_script", "retry": "design_cases", "human": "human_review"},
        )
        graph.add_edge("generate_script", "review_script")
        graph.add_conditional_edges(
            "review_script",
            lambda state: state["route"],
            {"approved": "contract_gate", "retry": "generate_script", "human": "human_review"},
        )
        graph.add_conditional_edges(
            "contract_gate",
            lambda state: state["route"],
            {"continue": "execute", "regenerate": "regenerate_affected", "human": "human_review"},
        )
        graph.add_edge("regenerate_affected", "contract_gate")
        graph.add_edge("execute", "review_results")
        graph.add_conditional_edges(
            "review_results",
            lambda state: state["route"],
            {"approved": "finalize", "retry": "execute", "human": "human_review"},
        )
        graph.add_conditional_edges(
            "human_review",
            lambda state: state["route"],
            {
                "parse_requirement": "parse_requirement",
                "design_cases": "design_cases",
                "generate_script": "generate_script",
                "regenerate_affected": "regenerate_affected",
                "execute": "execute",
                "finalize": "finalize",
            },
        )
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=self.checkpointer)

    def _close_after_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Return a graph result while retaining the connection for resume."""
        return result

    def invoke(self, initial_state: V2State | None = None) -> dict[str, Any]:
        """Run the graph and return the final merged state."""
        state: V2State = {
            "run_id": self.run_id,
            "output_dir": str(self.output_dir),
            "steps": [],
            "messages": [],
            "issues": [],
            "prev_signatures": {},
            "pending_repairs": {},
            "human_approved": False,
            "human_rejected": False,
        }
        if initial_state:
            state.update(initial_state)
        result = self.graph.invoke(state, config=self._config())
        if self.pending_interrupt() is not None:
            result["workflow_status"] = "WAITING_HUMAN"
            result["final_decision"] = "NEEDS_HUMAN"
        else:
            result["workflow_status"] = "COMPLETED"
        return result

    def resume(self, decision: HumanReviewDecision | dict[str, Any]) -> dict[str, Any]:
        """Resume the same run after ``human_review`` interrupted it."""
        if isinstance(decision, HumanReviewDecision):
            value = decision.model_dump()
        else:
            value = HumanReviewDecision.model_validate(decision).model_dump()
        result = self.graph.invoke(Command(resume=value), config=self._config())
        if self.pending_interrupt() is not None:
            result["workflow_status"] = "WAITING_HUMAN"
            result["final_decision"] = "NEEDS_HUMAN"
        else:
            result["workflow_status"] = "COMPLETED"
        return result

    def close(self) -> None:
        """Close the SQLite checkpoint connection owned by this workflow."""
        if self._checkpoint_connection is not None:
            self._checkpoint_connection.close()
            self._checkpoint_connection = None

    def _config(self) -> dict[str, Any]:
        return {"configurable": {"thread_id": self.run_id}}

    def pending_interrupt(self) -> Any:
        """Return the persisted interrupt payload, if this run is paused."""
        snapshot = self.graph.get_state(self._config())
        return snapshot.interrupts[0] if snapshot.interrupts else None

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
            attempt=self.repair.attempts + 1,
            decision=decision,
            routed_to=routed_to,
            detail=detail,
        ).model_dump()
        return {"steps": state.get("steps", []) + [record]}

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
        return {"messages": state.get("messages", []) + [message.model_dump()]}

    def _escalate(self, state: V2State, reason: str) -> dict[str, Any]:
        self.repair.escalate(reason)
        pending = {
            "reason": reason,
            "source_step": "workflow",
            "resume_target": "finalize",
            "kind": "review",
        }
        return {
            "issues": state.get("issues", []) + [reason],
            "pending_human_review": pending,
        }

    @staticmethod
    def _repair_target(target_step: str) -> str:
        """Translate repair bookkeeping names into graph node names."""
        return {
            "parse_requirement": "parse_requirement",
            "plan_cases": "design_cases",
            "generate_script": "generate_script",
            "rerun_failed": "execute",
            "regenerate_affected": "regenerate_affected",
        }.get(target_step, "finalize")

    def _escalate_for_human(
        self,
        state: V2State,
        reason: str,
        *,
        source_step: str,
        resume_target: str,
        kind: str = "review",
    ) -> dict[str, Any]:
        """Persist the human question context before entering ``interrupt``."""
        update = self._escalate(state, reason)
        update["pending_human_review"] = {
            "reason": reason,
            "source_step": source_step,
            "resume_target": resume_target,
            "kind": kind,
            "options": ["approve", "retry", "reject"],
            "artifacts": {
                name: str(self.output_dir / name)
                for name in ARTIFACT_NAMES
                if (self.output_dir / name).exists()
            },
        }
        write_json(
            self.output_dir / "human-review.json",
            {
                "run_id": self.run_id,
                "status": "waiting",
                "request": update["pending_human_review"],
            },
        )
        # ``interrupt`` stops before ``finalize`` runs, so preserve the
        # failure scene now rather than losing the evidence index while the
        # run is waiting for a human.
        self._preserve_failure_scene(state, "NEEDS_HUMAN")
        return update

    def human_review(self, state: V2State) -> dict[str, Any]:
        """Pause for a typed human decision and route the resumed run."""
        pending = state.get("pending_human_review") or {
            "reason": "workflow requires human review",
            "source_step": "workflow",
            "resume_target": "finalize",
            "kind": "review",
            "options": ["approve", "retry", "reject"],
        }
        decision = interrupt(pending, response_schema=HumanReviewDecision)
        if not isinstance(decision, HumanReviewDecision):
            decision = HumanReviewDecision.model_validate(decision)

        target = decision.target_step or str(pending.get("resume_target", "finalize"))
        if decision.action == "retry":
            route = self._repair_target(target)
            update: dict[str, Any] = {
                "route": route,
                "pending_human_review": {},
                "human_approved": True,
                "human_rejected": False,
                "issues": [
                    issue
                    for issue in state.get("issues", [])
                    if issue != pending.get("reason")
                ],
            }
        elif decision.action == "approve":
            # A breaking contract may continue only after approval, and the
            # approved path regenerates affected cases before re-running the gate.
            route = "regenerate_affected" if pending.get("kind") == "contract" else "finalize"
            update = {
                "route": route,
                "pending_human_review": {},
                "human_approved": True,
                "human_rejected": False,
                "issues": [
                    issue
                    for issue in state.get("issues", [])
                    if issue != pending.get("reason")
                ],
            }
        if decision.action in {"approve", "retry"}:
            self.repair.history.escalated_to_human = False
            self.repair.save()
        else:
            update = {
                "route": "finalize",
                "pending_human_review": {},
                "human_approved": False,
                "human_rejected": True,
                "final_decision": "NEEDS_HUMAN",
            }

        self.logger.log(
            "human_review",
            detail={
                "action": decision.action,
                "target_step": target,
                "comment": decision.comment,
            },
        )
        write_json(
            self.output_dir / "human-review.json",
            {
                "run_id": self.run_id,
                "status": "resumed",
                "request": pending,
                "decision": decision.model_dump(),
            },
        )
        return update

    def _record_pending_repair(
        self,
        state: V2State,
        loop_key: str,
        artifact: Path,
        *,
        after_signature: str,
    ) -> tuple[dict[str, Any], bool]:
        """Archive the artifact produced by the previous repair attempt."""
        pending = state.get("pending_repairs", {}).get(loop_key)
        if not pending:
            return {"pending_repairs": dict(state.get("pending_repairs", {}))}, False

        after = _read_json(artifact) or {}
        before = pending.get("before", {})
        no_progress = pending.get("before_signature", "") == after_signature
        self.repair.record(
            trigger=pending.get("trigger", "repair"),
            target_step=pending.get("target_step", ""),
            before=before,
            after=after,
            affected_case_ids=pending.get("affected_case_ids", []),
        )
        remaining = dict(state.get("pending_repairs", {}))
        remaining.pop(loop_key, None)
        return {"pending_repairs": remaining}, no_progress

    def _repair_route(
        self,
        state: V2State,
        *,
        loop_key: str,
        artifact: Path,
        trigger: str,
        target_step: str,
        signature: str | None = None,
    ) -> dict[str, Any]:
        """Decide retry vs human for a failed review, with bounded attempts.

        A retry that reproduces the exact previous result (no progress)
        escalates immediately instead of burning the remaining budget.
        Returns the node update carrying the route and remembered signatures.
        """
        current_signature = signature or _file_hash(artifact)
        previous = state.get("prev_signatures", {}).get(loop_key)
        signatures = dict(state.get("prev_signatures", {}))
        update: dict[str, Any] = {"prev_signatures": signatures}
        repair_update, no_progress = self._record_pending_repair(
            state, loop_key, artifact, after_signature=current_signature
        )
        update.update(repair_update)
        if no_progress or (previous is not None and previous == current_signature):
            update.update(
                self._escalate_for_human(
                    state,
                    f"{loop_key}: repair produced no progress",
                    source_step=target_step,
                    resume_target=self._repair_target(target_step),
                )
            )
            update["route"] = "human"
            return update
        if self.repair.exhausted:
            update.update(
                self._escalate_for_human(
                    state,
                    f"{loop_key}: max repair attempts ({self.repair.history.max_repair_attempts}) reached",
                    source_step=target_step,
                    resume_target=self._repair_target(target_step),
                )
            )
            update["route"] = "human"
            return update
        signatures[loop_key] = current_signature
        pending_repairs = dict(update.get("pending_repairs", {}))
        pending_repairs[loop_key] = {
            "before": _read_json(artifact) or {},
            "before_signature": current_signature,
            "trigger": trigger,
            "target_step": target_step,
        }
        update["pending_repairs"] = pending_repairs
        update["route"] = "retry"
        return update

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
            "case_designer" if approved else "requirement_parser",
            "requirement_review",
            str(self.output_dir / "requirement-review.json"),
            review.decision,
            review.issues,
        )
        if approved:
            update.update(self._trace(state, "review_requirement", "approved", "design_cases"))
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
            return update
        except Exception as exc:  # LLM 失败不阻断确定性覆盖，只记录问题
            update = self._trace(state, "analyze_requirements", "llm_error", "design_cases", f"{type(exc).__name__}: {exc}")
            return {"issues": state.get("issues", []) + [f"llm analysis failed: {type(exc).__name__}: {exc}"], **update}

    def design_cases(self, state: V2State) -> dict[str, Any]:
        """Case Design Agent: plan the standard case JSON from the baseline."""
        requirement = read_model(self.output_dir / "normalized-requirement.json", NormalizedRequirement)
        requirement_review = read_model(self.output_dir / "requirement-review.json", RequirementReview)
        cases = plan_cases(requirement, requirement_review, self.adapter)
        rules_path = self.output_dir / "llm-rules.json"
        if rules_path.exists():
            from api_agent.llm_rules import LLMRuleSet
            from api_agent.planner import compile_llm_cases

            rule_set = LLMRuleSet.model_validate_json(rules_path.read_text(encoding="utf-8"))
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
        elif self.repair.exhausted:
            update.update(
                self._escalate_for_human(
                    state,
                    "contract: max repair attempts reached on breaking changes",
                    source_step="contract_gate",
                    resume_target="finalize",
                )
            )
            update.update(self._trace(state, "contract_gate", report.status, "human"))
            update["route"] = "human"
        else:
            # 破坏性契约变化必须先经过 interrupt 人工确认，不能在同一轮
            # 自动再生后静默吸收为新基线。
            # 这条记录是契约漂移的审计快照，不计入自动修复预算；真正的
            # 选择性再生会在人工批准后由 regenerate_affected 记录 diff。
            self.repair.record(
                trigger="contract_breaking_change",
                target_step="human_review",
                before=baseline.model_dump(),
                after=runtime.model_dump(),
            )
            update.update(
                self._escalate_for_human(
                    state,
                    "contract: breaking changes need human confirmation before the baseline is regenerated",
                    source_step="contract_gate",
                    resume_target="regenerate_affected",
                    kind="contract",
                )
            )
            update.update(
                self._trace(
                    state,
                    "contract_gate",
                    report.status,
                    "human_review",
                    f"{len(report.changes)} changes require human confirmation",
                )
            )
            update["route"] = "human"
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
        self.repair.record(
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
        update = self._trace(state, "regenerate_affected", "done", "contract_gate", detail)
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
        escalated = self.repair.history.escalated_to_human
        failed_cases = 0
        inconclusive_cases = 0
        approved = False
        current_review = self._current_run_result_review()
        if current_review is not None:
            failed_cases = current_review.failed
            inconclusive_cases = current_review.inconclusive
            approved = current_review.decision == "approved"

        if state.get("human_rejected"):
            decision = "NEEDS_HUMAN"
        elif approved and not escalated:
            decision = "PASS"
        elif approved and state.get("human_approved"):
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
                attempt=self.repair.attempts + 1,
                decision=decision,
                routed_to="END",
            ).model_dump()
        ]
        workflow_report = WorkflowRunReport(
            run_id=self.run_id,
            finished_at=datetime.now(timezone.utc).isoformat(),
            decision=decision,
            max_repair_attempts=self.repair.history.max_repair_attempts,
            steps=steps,
            artifacts=artifacts,
            issues=state.get("issues", []),
        )
        write_model(self.output_dir / "workflow-report.json", workflow_report)
        if decision != "PASS":
            self._preserve_failure_scene(state, decision)
        return {"steps": steps, "final_decision": decision}

    def write_error_report(self, exc: BaseException) -> WorkflowRunReport:
        """Write a degraded report when the graph itself raises.

        Without this, a crashed node would leave no workflow-report.json and
        no failure scene, breaking the human-review escalation contract.
        """
        reason = f"workflow crashed: {type(exc).__name__}: {exc}"
        self.logger.log("step", agent="workflow", level="error", detail={"error": reason})
        self.repair.escalate(reason)
        workflow_report = WorkflowRunReport(
            run_id=self.run_id,
            finished_at=datetime.now(timezone.utc).isoformat(),
            decision="NEEDS_HUMAN",
            max_repair_attempts=self.repair.history.max_repair_attempts,
            steps=[],
            issues=[reason],
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
            "escalated_to_human": self.repair.history.escalated_to_human,
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
