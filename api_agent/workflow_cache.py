"""Small, auditable cache for deterministic workflow artifacts.

The cache is deliberately separate from LangGraph checkpoints.  Checkpoints
remember control-flow state; this file only remembers that a deterministic
artifact was produced for a particular input fingerprint.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from api_agent.openapi import canonical_hash


class WorkflowCache:
    """Persist stage fingerprints without caching run-specific evidence."""

    def __init__(self, output_dir: Path):
        self.path = output_dir / "workflow-cache.json"
        self.data: dict[str, Any] = {"schema_version": "1.0", "stages": {}}
        if self.path.exists():
            try:
                candidate = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(candidate, dict) and isinstance(candidate.get("stages"), dict):
                    self.data = candidate
            except (OSError, json.JSONDecodeError):
                # A corrupt cache is only a cache miss; it must never break a run.
                pass

    @staticmethod
    def fingerprint(value: Any) -> str:
        return canonical_hash(value)

    def hit(self, stage: str, key: str, artifacts: list[Path]) -> bool:
        record = self.data.get("stages", {}).get(stage)
        return bool(
            isinstance(record, dict)
            and record.get("key") == key
            and all(path.exists() for path in artifacts)
        )

    def store(self, stage: str, key: str, artifacts: list[Path]) -> None:
        self.data.setdefault("stages", {})[stage] = {
            "key": key,
            "artifacts": [str(path) for path in artifacts],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

