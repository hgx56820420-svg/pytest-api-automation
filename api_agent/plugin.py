"""pytest 插件入口：命令行参数、api / db / admin fixture、case 标记、失败证据。"""

import json
import os
import re
from pathlib import Path

import pytest

from api_agent.client import ApiClient, ApiTarget
from api_agent.contract import ContractValidator
from api_agent.db import Db, create_readonly_engine
from api_agent.evidence import Recorder

_recorder_key = pytest.StashKey[Recorder]()
_case_results_key = pytest.StashKey[dict]()
_MARKER = ".api-agent-evidence"


def _evidence_dir(config) -> Path:
    out_dir = Path(config.getoption("--api-evidence-dir"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / _MARKER).touch()
    return out_dir


def pytest_sessionstart(session):
    # 清掉上一次运行的证据，避免旧失败混进本次结果；只清插件自己建的目录
    out_dir = Path(session.config.getoption("--api-evidence-dir"))
    if (out_dir / _MARKER).exists():
        for old in out_dir.glob("*.json"):
            old.unlink()


def pytest_addoption(parser):
    group = parser.getgroup("api-agent")
    group.addoption("--api-base-url", default=os.getenv("API_BASE_URL"), help="被测服务地址（env: API_BASE_URL）")
    group.addoption("--api-db-url", default=os.getenv("API_DB_URL"), help="测试库只读连接（env: API_DB_URL）")
    group.addoption("--api-admin-user", default=os.getenv("API_ADMIN_USERNAME"), help="管理员账号（env: API_ADMIN_USERNAME）")
    group.addoption("--api-admin-password", default=os.getenv("API_ADMIN_PASSWORD"), help="管理员密码（env: API_ADMIN_PASSWORD）")
    group.addoption("--api-evidence-dir", default="artifacts/evidence", help="失败证据和 case-results.json 的输出目录")
    group.addoption(
        "--api-contract", default=os.getenv("API_CONTRACT"), help="接口基线 OpenAPI JSON；配置后每个响应都按契约校验（env: API_CONTRACT）"
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "case(id): 关联测试用例编号，例如 @pytest.mark.case('TC-ORDER-001')")
    config.stash[_case_results_key] = {}


@pytest.fixture(scope="session")
def api_target(pytestconfig) -> ApiTarget:
    """默认从命令行/环境变量读取被测服务。目录里的 conftest 可以覆盖它。"""
    base_url = pytestconfig.getoption("--api-base-url")
    if not base_url:
        pytest.fail("api-agent: set --api-base-url or API_BASE_URL", pytrace=False)
    return ApiTarget(
        base_url=base_url,
        db_url=pytestconfig.getoption("--api-db-url"),
        admin_username=pytestconfig.getoption("--api-admin-user"),
        admin_password=pytestconfig.getoption("--api-admin-password"),
        contract_path=pytestconfig.getoption("--api-contract"),
    )


@pytest.fixture
def api_recorder(request) -> Recorder:
    recorder = Recorder()
    request.node.stash[_recorder_key] = recorder
    return recorder


@pytest.fixture(scope="session")
def _contracts():
    # 按契约文件缓存，解析一次即可
    return {}


@pytest.fixture
def api(api_target, api_recorder, _contracts):
    contract = None
    if api_target.contract_path:
        if api_target.contract_path not in _contracts:
            _contracts[api_target.contract_path] = ContractValidator(api_target.contract_path)
        contract = _contracts[api_target.contract_path]
    client = ApiClient(api_target, api_recorder, contract=contract)
    yield client
    client.close()


@pytest.fixture(scope="session")
def _db_engines():
    # 按连接地址缓存：不同目录可能覆盖 api_target 指向不同服务，不能共用一个引擎
    engines: dict[str, object] = {}
    yield engines
    for engine in engines.values():
        engine.dispose()


@pytest.fixture
def db(api_target, _db_engines, api_recorder) -> Db:
    if not api_target.db_url:
        pytest.skip("api-agent: no database configured (--api-db-url)")
    if api_target.db_url not in _db_engines:
        _db_engines[api_target.db_url] = create_readonly_engine(api_target.db_url)
    return Db(_db_engines[api_target.db_url], api_recorder)


@pytest.fixture
def admin(api, api_target):
    if not (api_target.admin_username and api_target.admin_password):
        pytest.fail("api-agent: admin credentials not configured", pytrace=False)
    return api.login(api_target.admin_username, api_target.admin_password)


def _case_ids(item) -> list[str]:
    return [str(mark.args[0]) for mark in item.iter_markers("case") if mark.args]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" or (report.when == "setup" and not report.passed):
        results = item.config.stash[_case_results_key]
        for case_id in _case_ids(item):
            # 同一用例编号对应多条测试时，任何一条失败都算失败
            if results.get(case_id) != "failed":
                results[case_id] = report.outcome
    recorder = item.stash.get(_recorder_key, None)
    if report.failed and recorder is not None and recorder.entries:
        evidence = {"test": item.nodeid, "cases": _case_ids(item), "entries": recorder.entries}
        text = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
        report.sections.append(("api evidence", text))
        out_dir = _evidence_dir(item.config)
        (out_dir / (re.sub(r"[^A-Za-z0-9_.-]+", "_", item.nodeid)[-150:] + ".json")).write_text(text, encoding="utf-8")


def pytest_sessionfinish(session):
    results = session.config.stash.get(_case_results_key, {})
    if not results:
        return
    out_dir = _evidence_dir(session.config)
    (out_dir / "case-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
