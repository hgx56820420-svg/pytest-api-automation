"""Deterministic test-data cleanup tool with truth constraints.

设计约束（对应 roadmap "测试数据隔离、清理"）：

1. 只做确定性操作：固定 SQL 模板 + 白名单表名，Agent 不得注入自由 SQL。
2. 真实性约束：删除前必须 SELECT 确认账号存在；不存在则如实上报
   ``found=False``，绝不虚构删除成功，也不猜测/编造账号 ID。
3. 前缀护栏：只允许清理 ``prefix``（默认 ``agent_``）开头的测试账号，
   其他任何账号一律拒绝触碰。
4. 账号的业务子记录（订单/借阅/预约等）与账号在同一事务内一起删除，
   避免 FK 残留或半删状态。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text

from api_agent.models import CleanupSpec

SQLITE_PREFIX = "sqlite:///"


class CleanupTool:
    """Evidence-driven cleanup for one adapter's test database."""

    def __init__(self, database_url: str, spec: CleanupSpec):
        if not database_url.startswith(SQLITE_PREFIX):
            raise ValueError("CleanupTool only permits an explicit SQLite test database")
        self.spec = spec
        self.engine = create_engine(database_url)

    def find_user_id(self, username: str) -> int | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                text(
                    f"SELECT id FROM {self.spec.users_table} "
                    f"WHERE {self.spec.username_column} = :username"
                ),
                {"username": username},
            ).scalar_one_or_none()
        return int(row) if row is not None else None

    def cleanup_username(self, username: str) -> dict[str, Any]:
        """Clean one test account; returns an honest, structured result."""
        if not username.startswith(self.spec.prefix):
            return {"username": username, "found": False, "deleted": False, "reason": "prefix_guard_rejected"}

        user_id = self.find_user_id(username)
        if user_id is None:
            # 真实性约束：账号不存在就如实说不存在，不做任何写操作
            return {"username": username, "found": False, "deleted": False, "reason": "account_not_found"}

        children_deleted: dict[str, int] = {}
        with self.engine.begin() as connection:
            for table, fk_column in self.spec.children:
                result = connection.execute(
                    text(f"DELETE FROM {table} WHERE {fk_column} = :user_id"),
                    {"user_id": user_id},
                )
                children_deleted[table] = result.rowcount
            connection.execute(
                text(f"DELETE FROM {self.spec.users_table} WHERE id = :user_id"),
                {"user_id": user_id},
            )
        return {
            "username": username,
            "found": True,
            "deleted": True,
            "user_id": user_id,
            "children_deleted": children_deleted,
        }

    def close(self) -> None:
        self.engine.dispose()
