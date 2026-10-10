"""Bounded auto-repair bookkeeping with before/after diffs (V2)."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from api_agent.models import RepairAction, RepairHistory


class RepairManager:
    """Track repair attempts for one workflow run.

    Repairs are bounded: once ``max_repair_attempts`` is reached the workflow
    must escalate to a human review node instead of looping forever. Every
    attempt archives a unified diff of the affected artifact so a human can
    audit what changed before and after.
    """

    def __init__(self, output_dir: Path, max_repair_attempts: int, run_id: str = ""):
        self.path = output_dir / "repair-history.json"
        loaded = None
        if self.path.exists():
            try:
                candidate = RepairHistory.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
                # A legacy ledger without a run_id cannot be safely associated
                # with a new run.  Loading it would consume another run's
                # repair budget and contaminate its diff history.
                if not run_id or candidate.run_id == run_id:
                    loaded = candidate
            except (OSError, ValueError):
                # A corrupt ledger must not make the workflow silently lose its
                # audit boundary; start a fresh ledger and let the workflow
                # surface the resulting run in its normal reports.
                loaded = None
        self.history = loaded or RepairHistory(
            run_id=run_id, max_repair_attempts=max_repair_attempts
        )

    @property
    def attempts(self) -> int:
        # Escalation records are audit events, not automatic repair attempts.
        return sum(action.target_step != "human_review" for action in self.history.repairs)

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.history.max_repair_attempts

    def record(
        self,
        trigger: str,
        target_step: str,
        before: dict[str, Any],
        after: dict[str, Any],
        affected_case_ids: list[str] | None = None,
        reason: str = "",
    ) -> RepairAction:
        """Archive one repair attempt with hashes, summaries and a unified diff."""
        action = RepairAction(
            attempt=self.attempts + 1,
            trigger=trigger,
            target_step=target_step,
            reason=reason,
            affected_case_ids=affected_case_ids or [],
            before_summary={"hash": _hash(before), **_summary(before)},
            after_summary={"hash": _hash(after), **_summary(after)},
            diff=_diff(before, after),
        )
        self.history.repairs.append(action)
        self.save()
        return action

    def escalate(self, reason: str = "") -> None:
        """Mark the run as needing human review and persist the escalation."""
        if self.history.escalated_to_human:
            self.save()
            return
        self.history.escalated_to_human = True
        self.history.repairs.append(
            RepairAction(
                attempt=self.attempts + 1,
                trigger=reason or "escalated_to_human",
                target_step="human_review",
                reason=reason,
                before_summary={},
                after_summary={},
                diff="",
            )
        )
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.history.model_dump_json(indent=2), encoding="utf-8")


def _hash(value: dict[str, Any]) -> str:
    from api_agent.openapi import canonical_hash

    return canonical_hash(value)


def _summary(value: dict[str, Any]) -> dict[str, Any]:
    """Small human-readable projection used in repair records."""
    summary: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, list):
            summary[f"{key}_count"] = len(item)
    return summary


def _diff(before: dict[str, Any], after: dict[str, Any]) -> str:
    before_text = json.dumps(before, ensure_ascii=False, indent=2, sort_keys=True).splitlines(keepends=True)
    after_text = json.dumps(after, ensure_ascii=False, indent=2, sort_keys=True).splitlines(keepends=True)
    return "".join(difflib.unified_diff(before_text, after_text, fromfile="before", tofile="after"))
