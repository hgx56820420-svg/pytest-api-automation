"""演示服务的测试配置：每个服务在会话开始时用临时数据库起一个独立进程。

真实项目不需要这个文件，直接用 --api-base-url / --api-db-url 指向测试环境即可。
设置 DEMO_IN_PROCESS=1 时服务改为在测试进程内启动，给变异测试（mutmut）用。
"""

import importlib
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

from api_agent.client import ApiTarget

# 插件自身的测试用 pytester 在临时目录里跑 pytest
pytest_plugins = ["pytester"]

ROOT = Path(__file__).resolve().parent.parent
ADMIN_USERNAME = "admin_demo"
ADMIN_PASSWORD = "Admin123456"

# 服务名 -> (uvicorn 入口, 环境变量前缀)
SERVICES = {
    "mini_shop": ("examples.mini_shop.main:app", "APP"),
    "library": ("examples.library.main:app", "LIB"),
    "meeting": ("examples.meeting.main:app", "MEETING"),
}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_healthy(base_url: str, alive, describe_failure) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and alive():
        try:
            if requests.get(f"{base_url}/health", timeout=1).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    pytest.exit(f"demo service did not start: {base_url}\n{describe_failure()}", 1)


def _start_subprocess(entry: str, env: dict, port: int, work: Path):
    log_path = work / "server.log"
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", entry, "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env={**os.environ, **env},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    _wait_healthy(
        f"http://127.0.0.1:{port}",
        lambda: proc.poll() is None,
        lambda: log_path.read_text(encoding="utf-8", errors="replace"),
    )

    def stop():
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    return stop


def _start_in_process(entry: str, env: dict, port: int):
    """在测试进程的线程里跑服务：mutmut 只能看到本进程里执行的代码。

    mutmut 会在同一个进程里连续跑多次 pytest。服务模块第二次导入时用的是缓存，
    配置不会重新读取，所以数据库地址以模块里实际连接的为准，而不是本次新建的临时路径。
    """
    import uvicorn

    module_name, attr = entry.split(":")
    if module_name not in sys.modules:
        os.environ.update(env)  # 服务的配置在导入时读取环境变量，必须先设置再导入
    app = getattr(importlib.import_module(module_name), attr)
    package = module_name.rsplit(".", 1)[0]
    db_url = importlib.import_module(f"{package}.database").engine.url.render_as_string(hide_password=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    _wait_healthy(f"http://127.0.0.1:{port}", thread.is_alive, lambda: "server thread exited")

    def stop():
        server.should_exit = True
        thread.join(timeout=10)

    return stop, db_url


@pytest.fixture(scope="session")
def demo_service(tmp_path_factory):
    """按服务名启动演示服务，返回 ApiTarget。同一会话内每个服务只启动一次。"""
    started: dict[str, ApiTarget] = {}
    stops = []
    in_process = os.getenv("DEMO_IN_PROCESS") == "1"

    def start(name: str) -> ApiTarget:
        if name in started:
            return started[name]
        entry, prefix = SERVICES[name]
        work = tmp_path_factory.mktemp(name)
        db_path = (work / f"{name}.db").as_posix()
        port = _free_port()
        env = {
            f"{prefix}_DATABASE_URL": f"sqlite:///{db_path}",
            f"{prefix}_SECRET_KEY": secrets.token_urlsafe(32),
            f"{prefix}_ADMIN_USERNAME": ADMIN_USERNAME,
            f"{prefix}_ADMIN_PASSWORD": ADMIN_PASSWORD,
        }
        db_url = f"sqlite:///{db_path}"
        if in_process:
            stop, db_url = _start_in_process(entry, env, port)
        else:
            stop = _start_subprocess(entry, env, port, work)
        stops.append(stop)
        started[name] = ApiTarget(
            base_url=f"http://127.0.0.1:{port}",
            db_url=db_url,
            admin_username=ADMIN_USERNAME,
            admin_password=ADMIN_PASSWORD,
            # 每个响应都按仓库里的接口基线校验，实现和契约不一致时测试直接失败
            contract_path=str(ROOT / "contracts" / f"{name}.openapi.json"),
        )
        return started[name]

    yield start
    for stop in stops:
        stop()
