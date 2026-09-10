"""Read-only database observer for the Meeting Room test environment."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from api_agent.database import DatabaseObserver


class MeetingObserver(DatabaseObserver):
    """Evidence queries for rooms/bookings; inherits the sqlite-only guard."""

    def user(self, user_id: int) -> dict[str, Any]:
        return self._one("SELECT id, username, balance FROM users WHERE id = :id", user_id)

    def room(self, room_id: int) -> dict[str, Any]:
        return self._one(
            "SELECT id, name, capacity, hourly_price, status FROM rooms WHERE id = :id",
            room_id,
        )

    def booking(self, booking_id: int) -> dict[str, Any]:
        return self._one(
            "SELECT id, user_id, room_id, start_time, end_time, amount, status FROM bookings WHERE id = :id",
            booking_id,
        )

    def booking_count(self) -> int:
        with self._engine.connect() as connection:
            return int(connection.execute(text("SELECT COUNT(*) FROM bookings")).scalar_one())

    def table_count(self, table: str) -> int:
        if table != "rooms":
            raise ValueError("unsupported table")
        with self._engine.connect() as connection:
            return int(connection.execute(text("SELECT COUNT(*) FROM rooms")).scalar_one())
