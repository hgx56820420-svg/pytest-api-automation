"""Checkpointable workflow state; nodes return deltas to LangGraph reducers."""

from operator import add
from typing import Annotated, Any, TypedDict


class V2State(TypedDict, total=False):
    run_id: str
    output_dir: str
    settings_hash: str
    max_repair_attempts: int
    route: str
    steps: Annotated[list[dict[str, Any]], add]
    messages: Annotated[list[dict[str, Any]], add]
    issues: Annotated[list[str], add]
    repair_history: Annotated[list[dict[str, Any]], add]
    repair_escalated: bool
    prev_signatures: dict[str, str]
    final_decision: str
    llm_rules: dict[str, Any] | None


def repair_attempts(state: V2State) -> int:
    """An escalation is an audit event, not an automatic repair attempt."""
    return sum(item["target_step"] != "human_review" for item in state.get("repair_history", []))
