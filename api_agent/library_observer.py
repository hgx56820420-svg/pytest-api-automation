"""Read-only database observer for the Library test environment."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from api_agent.database import DatabaseObserver


class LibraryObserver(DatabaseObserver):
    """Evidence queries for books/borrows; inherits the sqlite-only guard."""

    def user(self, user_id: int) -> dict[str, Any]:
        return self._one("SELECT id, username, deposit FROM users WHERE id = :id", user_id)

    def book(self, book_id: int) -> dict[str, Any]:
        return self._one(
            "SELECT id, title, deposit, total_copies, available_copies, status FROM books WHERE id = :id",
            book_id,
        )

    def borrow(self, borrow_id: int) -> dict[str, Any]:
        return self._one(
            "SELECT id, user_id, book_id, deposit_charged, status FROM borrows WHERE id = :id",
            borrow_id,
        )

    def borrow_count(self) -> int:
        with self._engine.connect() as connection:
            return int(connection.execute(text("SELECT COUNT(*) FROM borrows")).scalar_one())

    def table_count(self, table: str) -> int:
        if table != "books":
            raise ValueError("unsupported table")
        with self._engine.connect() as connection:
            return int(connection.execute(text("SELECT COUNT(*) FROM books")).scalar_one())
