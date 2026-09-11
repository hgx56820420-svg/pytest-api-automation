"""Read-only database observer for test-environment evidence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text


class DatabaseObserver:
    def __init__(self, database_url: str):
        if not database_url.startswith("sqlite:///"):
            raise ValueError("V1 DatabaseObserver only permits an explicit SQLite test database")
        self._engine = create_engine(database_url)
        # 白名单：table -> 主键列。断言 DSL 只能查询白名单内的表。
        self.allowed_tables: dict[str, str] = {}

    def snapshot_row(self, table: str, key_id: int) -> dict[str, Any]:
        """Read one whitelisted row by its primary key (SELECT * semantics)."""
        if table not in self.allowed_tables:
            raise ValueError(f"table '{table}' is not in the observable whitelist")
        key_column = self.allowed_tables[table]
        with self._engine.connect() as connection:
            row = connection.execute(
                text(f"SELECT * FROM {table} WHERE {key_column} = :id"),
                {"id": key_id},
            ).mappings().one_or_none()
        return dict(row) if row else {}

    def row_count(self, table: str) -> int:
        if table not in self.allowed_tables:
            raise ValueError(f"table '{table}' is not in the observable whitelist")
        with self._engine.connect() as connection:
            return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one())

    def user(self, user_id: int) -> dict[str, Any]:
        return self._one("SELECT id, username, balance FROM users WHERE id = :id", user_id)

    def product(self, product_id: int) -> dict[str, Any]:
        return self._one("SELECT id, name, price, stock, status FROM products WHERE id = :id", product_id)

    def order(self, order_id: int) -> dict[str, Any]:
        return self._one(
            "SELECT id, user_id, product_id, quantity, amount, status FROM orders WHERE id = :id",
            order_id,
        )

    def order_count(self) -> int:
        with self._engine.connect() as connection:
            return int(connection.execute(text("SELECT COUNT(*) FROM orders")).scalar_one())

    def cart_items(self, user_id: int) -> list[dict[str, Any]]:
        return self._many(
            "SELECT id, user_id, product_id, quantity FROM cart_items WHERE user_id = :id ORDER BY id",
            user_id,
        )

    def coupon(self, code: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, code, discount_percent, max_uses, used_count, status FROM coupons WHERE code = :code"),
                {"code": code},
            ).mappings().one_or_none()
        return dict(row) if row else {}

    def inventory_transactions(self, product_id: int) -> list[dict[str, Any]]:
        return self._many(
            "SELECT id, product_id, quantity_change, reason, reference_id FROM inventory_transactions WHERE product_id = :id ORDER BY id",
            product_id,
        )

    def _many(self, statement: str, identity: int) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = connection.execute(text(statement), {"id": identity}).mappings().all()
        return [dict(row) for row in rows]

    def _one(self, statement: str, identity: int) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(text(statement), {"id": identity}).mappings().one_or_none()
        return dict(row) if row else {}

    def close(self) -> None:
        self._engine.dispose()
