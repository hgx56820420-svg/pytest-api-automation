"""插件自身的安全护栏，不需要启动服务。"""

import sqlite3

import pytest

from api_agent.client import ApiClient, ApiTarget, UnsafePathError
from api_agent.db import Db, create_readonly_engine
from api_agent.evidence import REDACTED, Recorder, redact


@pytest.fixture
def client():
    client = ApiClient(ApiTarget(base_url="http://127.0.0.1:8010"), Recorder())
    yield client
    client.close()


@pytest.mark.parametrize("path", ["@evil.host/x", "//evil.host/x", "http://evil.host/x", "api/orders", "/\\evil.host"])
def test_paths_that_could_leave_the_target_host_are_rejected(client, path):
    with pytest.raises(UnsafePathError):
        client.get(path)


def test_redact_hides_secrets_at_any_depth():
    data = {"username": "u", "password": "p", "nested": [{"access_token": "t", "refresh_token": "r", "id": 1}]}

    assert redact(data) == {
        "username": "u",
        "password": REDACTED,
        "nested": [{"access_token": REDACTED, "refresh_token": REDACTED, "id": 1}],
    }


@pytest.fixture
def sqlite_db(tmp_path):
    path = tmp_path / "t.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO users (id, name) VALUES (1, 'a')")
    engine = create_readonly_engine(f"sqlite:///{path.as_posix()}")
    yield engine
    engine.dispose()


def test_db_reads_rows_and_counts(sqlite_db):
    db = Db(sqlite_db, Recorder())

    assert db.row("users", 1) == {"id": 1, "name": "a"}
    assert db.count("users", name="a") == 1
    assert db.row("users", 2) is None


def test_db_rejects_injected_identifiers(sqlite_db):
    db = Db(sqlite_db, Recorder())

    with pytest.raises(ValueError):
        db.rows("users; DROP TABLE users")


def test_db_connection_is_read_only(sqlite_db):
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    with sqlite_db.connect() as conn, pytest.raises(OperationalError, match="readonly"):
        conn.execute(text("DELETE FROM users"))
