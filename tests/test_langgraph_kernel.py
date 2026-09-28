"""Behavior at LangGraph checkpoint, reducer and domain-repair boundaries."""

import json

import pytest
from langgraph.graph import END, START, StateGraph

from api_agent import workflow as workflow_module
from api_agent.graph_state import V2State, repair_attempts
from api_agent.workflow import V2Workflow
from test_agent_v2 import make_stub_run, make_workflow


def restored_workflow(original, **changes):
    settings = {name: getattr(original, name) for name in (
        "output_dir", "openapi_source", "requirements_md", "base_url", "database_url",
        "runtime_openapi", "max_repair_attempts", "adapter", "fixed_accounts",
        "llm_analysis", "run_id", "checkpointer",
    )}
    return V2Workflow(**(settings | changes))


def test_pause_resume_with_new_instance_does_not_repeat_completed_nodes(tmp_path, monkeypatch):
    workflow = restored_workflow(make_workflow(tmp_path, monkeypatch), interrupt_before=["execute"])
    paused = workflow.invoke()
    assert "final_decision" not in paused
    assert not (workflow.output_dir / "execution-report.json").exists()
    assert workflow.graph.get_state(workflow._config()).next == ("execute",)

    restored = restored_workflow(workflow)
    result = restored.resume()
    assert result["final_decision"] == "PASS"
    assert len(result["steps"]) == 11
    assert sum(s["step"] == "parse_requirement" for s in result["steps"]) == 1
    report = json.loads((workflow.output_dir / "workflow-report.json").read_text(encoding="utf-8"))
    assert result["steps"] == report["steps"]
    assert restored.resume() == result  # completed resume must not execute again
    with pytest.raises(ValueError, match="already has a checkpoint"):
        restored.invoke()


def test_crash_resume_preserves_repair_budget_and_no_progress_signature(tmp_path, monkeypatch):
    workflow = make_workflow(tmp_path, monkeypatch)
    execute = make_stub_run(failing_case_ids=["operation.create_order_api_orders_post"])
    calls = 0

    def crash_on_second_execution(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("executor unavailable before sending requests")
        return execute(*args, **kwargs)

    monkeypatch.setattr(workflow_module, "run_pipeline", crash_on_second_execution)
    with pytest.raises(RuntimeError, match="executor unavailable"):
        workflow.invoke()
    snapshot = workflow.graph.get_state(workflow._config())
    assert snapshot.next == ("execute",)
    assert repair_attempts(snapshot.values) == 1
    assert snapshot.values["prev_signatures"]["results"]
    error_report = workflow.write_error_report(RuntimeError("interrupted"))
    assert error_report.steps  # crash reporting must keep the completed trace

    restored = restored_workflow(workflow)
    result = restored.resume()
    assert result["final_decision"] == "FAIL"
    assert restored.repair.attempts == 1
    assert result["repair_escalated"] is True
    assert len(result["repair_history"]) == 2  # one repair, one escalation
    assert len(result["issues"]) == 1
    assert "no progress" in result["issues"][0]
    assert sum(s["step"] == "parse_requirement" for s in result["steps"]) == 1
    assert calls == 3


def test_zero_repair_budget_escalates_without_consuming_an_attempt(tmp_path, monkeypatch):
    workflow = make_workflow(tmp_path, monkeypatch, max_repair_attempts=0,
                             failing_case_ids=["operation.create_order_api_orders_post"])
    result = workflow.invoke()
    assert result["final_decision"] == "FAIL"
    assert workflow.repair.attempts == 0
    assert result["repair_escalated"]
    assert sum(s["step"] == "executor" for s in result["steps"]) == 1


def test_checkpoint_identity_and_settings_are_validated(tmp_path, monkeypatch):
    workflow = restored_workflow(make_workflow(tmp_path, monkeypatch), interrupt_before=["execute"])
    with pytest.raises(ValueError, match="thread_id must equal"):
        workflow.invoke(config={"configurable": {"thread_id": "another-run"}})
    with pytest.raises(ValueError, match="cannot override run_id"):
        workflow.invoke({"run_id": "another-run"})
    with pytest.raises(ValueError, match="No checkpoint"):
        workflow.resume()
    workflow.invoke()
    with pytest.raises(ValueError, match="settings differ"):
        restored_workflow(workflow, max_repair_attempts=10).resume()


def test_separate_runs_do_not_share_history_on_same_checkpointer(tmp_path, monkeypatch):
    first = make_workflow(tmp_path, monkeypatch, max_repair_attempts=0,
                          failing_case_ids=["operation.create_order_api_orders_post"])
    assert first.invoke()["repair_escalated"]
    monkeypatch.setattr(workflow_module, "run_pipeline", make_stub_run())
    second = restored_workflow(first, run_id="independent", output_dir=tmp_path / "second")
    result = second.invoke()
    assert result["final_decision"] == "PASS"
    assert not result["repair_history"] and not result["issues"]
    assert first.repair.history.escalated_to_human


def test_reused_artifact_directory_does_not_load_stale_llm_rules(tmp_path, monkeypatch):
    workflow = make_workflow(tmp_path, monkeypatch)
    workflow.output_dir.mkdir(parents=True, exist_ok=True)
    (workflow.output_dir / "llm-rules.json").write_text("not valid rules from old run", encoding="utf-8")
    result = workflow.invoke()
    assert result["final_decision"] == "PASS"
    cases = json.loads((workflow.output_dir / "test-cases.json").read_text(encoding="utf-8"))
    assert all(case["scenario"] != "llm" for case in cases["cases"])
    assert json.loads((workflow.output_dir / "llm-rules.json").read_text(encoding="utf-8")) == {"rules": []}


def test_langgraph_reducers_merge_parallel_node_deltas():
    builder = StateGraph(V2State)
    for name in ("left", "right"):
        builder.add_node(name, lambda state, name=name: {"steps": [{"step": name}], "issues": [name]})
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    result = builder.compile().invoke({"steps": [{"step": "initial"}], "issues": []})
    assert sorted(s["step"] for s in result["steps"]) == ["initial", "left", "right"]
    assert sorted(result["issues"]) == ["left", "right"]


def test_large_domain_repair_budget_does_not_hit_default_graph_limit(tmp_path, monkeypatch):
    workflow = make_workflow(tmp_path, monkeypatch, max_repair_attempts=8)
    review = workflow_module.review_markdown_requirements
    reviews = 0

    def improving_review(*args, **kwargs):
        nonlocal reviews
        reviews += 1
        result = review(*args, **kwargs)
        if reviews <= 8:
            result.decision = "needs_revision"
            result.issues = [f"unresolved item {reviews}"]
        return result

    monkeypatch.setattr(workflow_module, "review_markdown_requirements", improving_review)
    result = workflow.invoke()
    assert result["final_decision"] == "PASS"
    assert workflow.repair.attempts == 8
    assert len(result["steps"]) > 25


@pytest.mark.parametrize("model_fails", [False, True])
def test_analyst_subgraph_runs_inside_checkpointed_parent(tmp_path, monkeypatch, model_fails):
    from types import SimpleNamespace
    from api_agent import llm_analyst
    from api_agent.llm_rules import LLMRuleSet

    def model_response(messages):
        if model_fails:
            raise RuntimeError("provider is unavailable")
        return LLMRuleSet()

    model = SimpleNamespace(with_structured_output=lambda *a, **kw: SimpleNamespace(invoke=model_response))
    monkeypatch.setattr(llm_analyst, "get_chat_model", lambda: model)
    monkeypatch.setattr(workflow_module, "is_llm_enabled", lambda: True)
    workflow = restored_workflow(make_workflow(tmp_path, monkeypatch), llm_analysis=True)
    result = workflow.invoke()
    assert result["final_decision"] == "PASS"
    analyst_step = next(s for s in result["steps"] if s["step"] == "analyze_requirements")
    assert analyst_step["decision"] == ("llm_error" if model_fails else "done")
    assert result["llm_rules"] == (None if model_fails else {"rules": []})
    assert len(result["issues"]) == int(model_fails)
