"""Read-only database observer for test-environment evidence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text


class DatabaseObserver:
    def __init__(self, database_url: str):
        if not database_url.startswith("sqlite:///"):
            raise ValueError("V1 DatabaseObserver only permits an explicit SQLite test database")
        self._engine = create_engine(database_url)

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

    def _one(self, statement: str, identity: int) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = connection.execute(text(statement), {"id": identity}).mappings().one_or_none()
        return dict(row) if row else {}

    def close(self) -> None:
        self._engine.dispose()

