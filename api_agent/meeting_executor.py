"""Meeting Room scenario executor: booking/cancel flows with time conflicts."""

from __future__ import annotations

import uuid
from typing import Any

from api_agent.executor import ScenarioExecutor, _request_view, _response_view
from api_agent.meeting_observer import MeetingObserver

# 固定远期日期保证确定性；相邻时段（11-12 点）不重叠，可复用同一会议室
BOOKING_DAY = "2031-03-01"
SLOT_A_START = f"{BOOKING_DAY}T10:00:00"
SLOT_A_END = f"{BOOKING_DAY}T11:00:00"
SLOT_B_START = f"{BOOKING_DAY}T11:00:00"
SLOT_B_END = f"{BOOKING_DAY}T12:00:00"
SLOT_OVERLAP_START = f"{BOOKING_DAY}T10:30:00"
SLOT_OVERLAP_END = f"{BOOKING_DAY}T11:30:00"


class MeetingExecutor(ScenarioExecutor):
    """Same evidence pipeline as Mini Shop, Meeting Room domain scenarios."""

    observer_class = MeetingObserver

    # -- helpers ---------------------------------------------------------

    def _create_room(self, headers: dict[str, str], *, hourly_price: float = 10.0) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/api/rooms",
            headers=headers,
            json_body={
                "name": f"agent-room-{uuid.uuid4().hex[:8]}",
                "capacity": 10,
                "hourly_price": hourly_price,
            },
        )
        response.raise_for_status()
        return response.json()

    def _create_booking(self, headers: dict[str, str], room_id: int, start: str, end: str) -> Any:
        return self._request(
            "POST",
            "/api/bookings",
            headers=headers,
            json_body={"room_id": room_id, "start_time": start, "end_time": end},
        )

    def _room_setup(self, *, hourly_price: float = 10.0):
        user, headers = self._register_and_auth()
        room = self._create_room(headers, hourly_price=hourly_price)
        return user, headers, room

    def _assert_booking_created(self, assertions, before, after, amount):
        self._assert(
            assertions,
            "booking_created",
            bool(after["booking"]) and after["booking_count"] == before["booking_count"] + 1,
            before["booking_count"] + 1,
            after["booking_count"],
        )
        expected_balance = round(before["user"].get("balance") - amount, 2)
        self._assert(
            assertions,
            "balance_decreased",
            after["user"].get("balance") == expected_balance,
            expected_balance,
            after["user"].get("balance"),
        )
        self._assert(
            assertions,
            "booking_amount_matches",
            after["booking"].get("amount") == amount,
            amount,
            after["booking"].get("amount"),
        )

    def _assert_no_booking_side_effect(self, assertions, before, after):
        self._assert(
            assertions,
            "balance_unchanged",
            after["user"].get("balance") == before["user"].get("balance"),
            before["user"].get("balance"),
            after["user"].get("balance"),
        )
        self._assert(
            assertions,
            "booking_not_created",
            after["booking_count"] == before["booking_count"],
            before["booking_count"],
            after["booking_count"],
        )

    # -- main-case scenarios ----------------------------------------------

    def _scenario_list_rooms(self, case, assertions):
        return self._simple(case, assertions, "GET", "/api/rooms")

    def _scenario_create_room(self, case, assertions):
        _, headers = self._register_and_auth()
        body = {"name": f"agent-room-{uuid.uuid4().hex[:8]}", "capacity": 10, "hourly_price": 10.0}
        before = {"count": self.db.table_count("rooms")}
        response = self._request("POST", "/api/rooms", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        room_id = response.json().get("id") if response.ok else None
        after_room = self.db.room(room_id) if room_id else {}
        self._assert(assertions, "room_created", bool(after_room), True, bool(after_room))
        after = {"count": self.db.table_count("rooms")}
        self._assert(
            assertions,
            "room_count_increased",
            after["count"] == before["count"] + 1,
            before["count"] + 1,
            after["count"],
        )
        return (
            _request_view("POST", "/api/rooms", {"json_body": body}),
            _response_view(response),
            {"count": before["count"]},
            {"room": after_room, "count": after["count"]},
        )

    def _scenario_get_room(self, case, assertions):
        _, headers, room = self._room_setup()
        return self._simple(case, assertions, "GET", f"/api/rooms/{room['id']}")

    def _scenario_update_room(self, case, assertions):
        _, headers, room = self._room_setup()
        before = {"room": self.db.room(room["id"])}
        body = {"capacity": 20, "hourly_price": 15.0}
        response = self._request("PUT", f"/api/rooms/{room['id']}", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        after = {"room": self.db.room(room["id"])}
        changed = after["room"].get("capacity") == 20 and after["room"].get("hourly_price") == 15.0
        self._assert(assertions, "room_updated", changed, body, after["room"])
        return (
            _request_view("PUT", f"/api/rooms/{room['id']}", {"json_body": body}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_disable_room(self, case, assertions):
        _, headers, room = self._room_setup()
        before = {"room": self.db.room(room["id"])}
        response = self._request("DELETE", f"/api/rooms/{room['id']}", headers=headers)
        self._check_http(case, response, assertions)
        after = {"room": self.db.room(room["id"])}
        self._assert(
            assertions,
            "room_disabled",
            after["room"].get("status") == "disabled",
            "disabled",
            after["room"].get("status"),
        )
        return (
            _request_view("DELETE", f"/api/rooms/{room['id']}", {}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_create_booking(self, case, assertions):
        user, headers, room = self._room_setup()
        before = {
            "user": self.db.user(user["id"]),
            "booking_count": self.db.booking_count(),
        }
        response = self._create_booking(headers, room["id"], SLOT_A_START, SLOT_A_END)
        self._check_http(case, response, assertions)
        booking_id = response.json().get("id") if response.ok else None
        after = {
            "user": self.db.user(user["id"]),
            "booking": self.db.booking(booking_id),
            "booking_count": self.db.booking_count(),
        }
        self._assert_booking_created(assertions, before, after, room["hourly_price"])
        return (
            _request_view(
                "POST",
                "/api/bookings",
                {"json_body": {"room_id": room["id"], "start_time": SLOT_A_START, "end_time": SLOT_A_END}},
            ),
            _response_view(response),
            before,
            after,
        )

    def _scenario_list_bookings(self, case, assertions):
        _, headers, room = self._room_setup()
        created = self._create_booking(headers, room["id"], SLOT_A_START, SLOT_A_END)
        created.raise_for_status()
        result = self._simple(case, assertions, "GET", "/api/bookings", headers=headers)
        ids = [item["id"] for item in result[1].get("json", {}).get("items", [])]
        self._assert(assertions, "booking_in_list", created.json()["id"] in ids, created.json()["id"], ids)
        return result

    def _scenario_cancel_booking(self, case, assertions):
        user, headers, room = self._room_setup()
        created = self._create_booking(headers, room["id"], SLOT_A_START, SLOT_A_END)
        created.raise_for_status()
        booking_id = created.json()["id"]
        before = {"user": self.db.user(user["id"]), "booking": self.db.booking(booking_id)}
        response = self._request("POST", f"/api/bookings/{booking_id}/cancel", headers=headers)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "booking": self.db.booking(booking_id)}
        self._assert(
            assertions,
            "booking_status_cancelled",
            after["booking"].get("status") == "cancelled",
            "cancelled",
            after["booking"].get("status"),
        )
        expected_balance = round(before["user"].get("balance") + created.json()["amount"], 2)
        self._assert(
            assertions,
            "balance_restored",
            after["user"].get("balance") == expected_balance,
            expected_balance,
            after["user"].get("balance"),
        )
        return (
            _request_view("POST", f"/api/bookings/{booking_id}/cancel", {}),
            _response_view(response),
            before,
            after,
        )

    # -- negative scenarios ------------------------------------------------

    def _scenario_create_room_missing_token(self, case, assertions):
        body = {"name": f"agent-room-{uuid.uuid4().hex[:8]}", "capacity": 10, "hourly_price": 10.0}
        before = {"count": self.db.table_count("rooms")}
        response = self._request("POST", "/api/rooms", json_body=body)
        self._check_http(case, response, assertions)
        after = {"count": self.db.table_count("rooms")}
        self._assert(assertions, "room_not_created", before["count"] == after["count"], before["count"], after["count"])
        return _request_view("POST", "/api/rooms", {"json_body": body}), _response_view(response), before, after

    def _scenario_get_room_not_found(self, case, assertions):
        return self._simple(case, assertions, "GET", "/api/rooms/999999")

    def _scenario_list_rooms_invalid_pagination(self, case, assertions):
        statuses = []
        for params in ({"page": 0}, {"size": 101}):
            statuses.append(self._request("GET", "/api/rooms", params=params).status_code)
        self._assert(assertions, "pagination_boundaries", statuses == [422, 422], [422, 422], statuses)
        return {"method": "GET", "path": "/api/rooms", "params": "invalid boundaries"}, {"status_codes": statuses}, {}, {}

    def _scenario_booking_room_not_found(self, case, assertions):
        user, headers = self._register_and_auth()
        before = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        response = self._create_booking(headers, 999999, SLOT_A_START, SLOT_A_END)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        self._assert_no_booking_side_effect(assertions, before, after)
        return (
            _request_view("POST", "/api/bookings", {"json_body": {"room_id": 999999}}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_booking_disabled_room(self, case, assertions):
        user, headers, room = self._room_setup()
        self._request("DELETE", f"/api/rooms/{room['id']}", headers=headers).raise_for_status()
        before = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        response = self._create_booking(headers, room["id"], SLOT_A_START, SLOT_A_END)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        self._assert_no_booking_side_effect(assertions, before, after)
        return (
            _request_view("POST", "/api/bookings", {"json_body": {"room_id": room["id"]}}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_booking_time_conflict(self, case, assertions):
        """核心新维度：与既有 booked 预约时间重叠必须 409 且无副作用。"""
        user, headers, room = self._room_setup()
        first = self._create_booking(headers, room["id"], SLOT_A_START, SLOT_A_END)
        first.raise_for_status()
        before = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        response = self._create_booking(headers, room["id"], SLOT_OVERLAP_START, SLOT_OVERLAP_END)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        self._assert_no_booking_side_effect(assertions, before, after)
        return (
            _request_view("POST", "/api/bookings", {"json_body": {"room_id": room["id"], "start_time": SLOT_OVERLAP_START}}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_booking_adjacent_slot_allowed(self, case, assertions):
        """相邻时段（11-12 点）不算冲突，应成功且金额正确。"""
        user, headers, room = self._room_setup()
        before = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        response = self._create_booking(headers, room["id"], SLOT_B_START, SLOT_B_END)
        self._check_http(case, response, assertions)
        booking_id = response.json().get("id") if response.ok else None
        after = {
            "user": self.db.user(user["id"]),
            "booking": self.db.booking(booking_id),
            "booking_count": self.db.booking_count(),
        }
        self._assert_booking_created(assertions, before, after, room["hourly_price"])
        return (
            _request_view("POST", "/api/bookings", {"json_body": {"room_id": room["id"], "start_time": SLOT_B_START}}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_booking_invalid_time_range(self, case, assertions):
        user, headers, room = self._room_setup()
        before = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        response = self._create_booking(headers, room["id"], SLOT_A_END, SLOT_A_START)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        self._assert_no_booking_side_effect(assertions, before, after)
        return (
            _request_view("POST", "/api/bookings", {"json_body": {"room_id": room["id"], "start_time": SLOT_A_END, "end_time": SLOT_A_START}}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_booking_insufficient_balance(self, case, assertions):
        user, headers, room = self._room_setup(hourly_price=2000.0)
        before = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        response = self._create_booking(headers, room["id"], SLOT_A_START, SLOT_A_END)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        self._assert_no_booking_side_effect(assertions, before, after)
        return (
            _request_view("POST", "/api/bookings", {"json_body": {"room_id": room["id"]}}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_booking_missing_token(self, case, assertions):
        user, headers, room = self._room_setup()
        before = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        response = self._request(
            "POST",
            "/api/bookings",
            json_body={"room_id": room["id"], "start_time": SLOT_A_START, "end_time": SLOT_A_END},
        )
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "booking_count": self.db.booking_count()}
        self._assert_no_booking_side_effect(assertions, before, after)
        return (
            _request_view("POST", "/api/bookings", {"json_body": {"room_id": room["id"]}}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_cancel_twice(self, case, assertions):
        user, headers, room = self._room_setup()
        created = self._create_booking(headers, room["id"], SLOT_A_START, SLOT_A_END)
        created.raise_for_status()
        booking_id = created.json()["id"]
        self._request("POST", f"/api/bookings/{booking_id}/cancel", headers=headers).raise_for_status()
        before = {"user": self.db.user(user["id"]), "booking": self.db.booking(booking_id)}
        response = self._request("POST", f"/api/bookings/{booking_id}/cancel", headers=headers)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "booking": self.db.booking(booking_id)}
        self._assert(assertions, "state_unchanged", before == after, before, after)
        return (
            _request_view("POST", f"/api/bookings/{booking_id}/cancel", {}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_cancel_other_user(self, case, assertions):
        _, headers_a, room = self._room_setup()
        created = self._create_booking(headers_a, room["id"], SLOT_A_START, SLOT_A_END)
        created.raise_for_status()
        _, headers_b = self._register_and_auth()
        before = {"booking": self.db.booking(created.json()["id"])}
        response = self._request("POST", f"/api/bookings/{created.json()['id']}/cancel", headers=headers_b)
        self._check_http(case, response, assertions)
        self._assert(assertions, "booking_hidden", response.status_code == 404, 404, response.status_code)
        after = {"booking": self.db.booking(created.json()["id"])}
        self._assert(assertions, "state_unchanged", before == after, before, after)
        return (
            _request_view("POST", f"/api/bookings/{created.json()['id']}/cancel", {}),
            _response_view(response),
            before,
            after,
        )
