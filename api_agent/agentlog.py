"""Structured JSONL logging with run/case/operation/request correlation (V2)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class AgentLogger:
    """Append-only JSONL logger shared by every V2 agent and the executor.

    Every record carries the correlation ids required by the V2 roadmap so
    that logs, HTTP evidence and database snapshots can be joined on
    ``request_id`` after a run.
    """

    def __init__(self, path: Path, run_id: str):
        self.path = path
        self.run_id = run_id
        path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        event: str,
        *,
        level: str = "info",
        agent: str = "",
        case_id: str = "",
        operation_id: str = "",
        request_id: str = "",
        detail: Any = None,
    ) -> None:
        """Append one JSON record with the full correlation id set."""
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.perf_counter() * 1000) % 1000:03d}Z",
            "level": level,
            "event": event,
            "run_id": self.run_id,
            "agent": agent,
            "case_id": case_id,
            "operation_id": operation_id,
            "request_id": request_id,
            "detail": detail,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        """Parse the whole JSONL file; corrupt lines are skipped with a note."""
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append(
                    {"level": "error", "event": "corrupt_log_line", "run_id": self.run_id, "detail": line[:200]}
                )
        return records

    def requests_for_case(self, case_id: str) -> list[str]:
        """Return request_ids correlated to a case, used by the result reviewer."""
        return [
            record["request_id"]
            for record in self.read_all()
            if record.get("case_id") == case_id and record.get("request_id")
        ]
