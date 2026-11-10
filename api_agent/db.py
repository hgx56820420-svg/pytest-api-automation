"""数据库观察器：只读，只提供按条件查询，用来断言接口的数据库副作用。

SQLite 以只读模式打开；其他数据库请给测试配只读账号，这里不做写操作。
表名和列名只允许普通标识符，值一律走参数绑定。
"""

import re
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from api_agent.evidence import Recorder

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def _readonly_url(url: str) -> str:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        return url
    path = parsed.database.replace("\\", "/")
    return f"sqlite:///file:{path}?mode=ro&uri=true"


def create_readonly_engine(url: str):
    # NullPool：每次查询拿新连接，读到的一定是服务刚提交的数据
    return create_engine(_readonly_url(url), poolclass=NullPool)


class Db:
    def __init__(self, engine, recorder: Recorder):
        self.engine = engine
        self.recorder = recorder

    def _select(self, table: str, where: dict[str, Any], *, columns: str = "*") -> list[dict[str, Any]]:
        sql = f"SELECT {columns} FROM {_ident(table)}"
        if where:
            sql += " WHERE " + " AND ".join(f"{_ident(k)} = :{k}" for k in where)
        with self.engine.connect() as conn:
            rows = [dict(row._mapping) for row in conn.execute(text(sql), where)]
        self.recorder.db(query=sql, params=where, result=rows)
        return rows

    def rows(self, table: str, **where: Any) -> list[dict[str, Any]]:
        return self._select(table, where)

    def row(self, table: str, id: Any, key: str = "id") -> dict[str, Any] | None:
        rows = self._select(table, {key: id})
        return rows[0] if rows else None

    def count(self, table: str, **where: Any) -> int:
        return self._select(table, where, columns="COUNT(*) AS n")[0]["n"]
