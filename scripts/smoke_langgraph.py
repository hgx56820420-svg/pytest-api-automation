"""Run the real V2 HTTP/SQLite workflow against all three local demo services.

Usage from the repository root: python scripts/smoke_langgraph.py
Each run keeps isolated databases, server logs and evidence under artifacts/.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api_agent.workflow import V2Workflow  # noqa: E402


def run_smoke() -> list[dict]:
    os.chdir(ROOT)
    results = []
    for adapter, module, variable, document in (
        ("mini_shop", "app.main:app", "APP_DATABASE_URL", "MINI_SHOP"),
        ("library", "services.library.main:app", "LIB_DATABASE_URL", "LIBRARY"),
        ("meeting", "services.meeting.main:app", "MEETING_DATABASE_URL", "MEETING"),
    ):
        run_id = f"smoke-{adapter}-{uuid.uuid4().hex[:8]}"
        output = ROOT / "artifacts" / run_id
        output.mkdir(parents=True)
        database_url = f"sqlite:///{(output / 'service.db').as_posix()}"
        with socket.socket() as port_reservation:
            port_reservation.bind(("127.0.0.1", 0))
            port = port_reservation.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        environment = {**os.environ, variable: database_url, "PYTHONUTF8": "1"}
        with (output / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1", "--port", str(port)],
                cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    if server.poll() is not None:
                        raise RuntimeError(f"{adapter} server exited; see {output / 'server.log'}")
                    try:
                        if requests.get(f"{base_url}/openapi.json", timeout=1).ok:
                            break
                    except requests.RequestException:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"{adapter} server did not start")
                    time.sleep(0.1)
                workflow = V2Workflow(
                    output_dir=output, openapi_source=f"{base_url}/openapi.json",
                    requirements_md=ROOT / "docs" / f"{document}_API_REQUIREMENTS.md",
                    base_url=base_url, database_url=database_url,
                    adapter=adapter, run_id=run_id,
                )
                state = workflow.invoke()
                execution = json.loads((output / "execution-report.json").read_text(encoding="utf-8"))
                result = {"adapter": adapter, "decision": state["final_decision"],
                          "summary": execution["summary"], "output": str(output)}
                results.append(result)
                print(json.dumps(result), flush=True)
                if state["final_decision"] != "PASS":
                    raise AssertionError(f"{adapter} workflow failed; inspect {output}")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=10)
    return results


if __name__ == "__main__":
    run_smoke()
