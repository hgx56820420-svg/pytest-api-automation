"""Library scenario executor: borrow/return flows with side-effect evidence."""

from __future__ import annotations

import uuid
from typing import Any

from api_agent.executor import ScenarioExecutor, _request_view, _response_view
from api_agent.library_observer import LibraryObserver
from api_agent.models import TestCase


class LibraryExecutor(ScenarioExecutor):
    """Same evidence pipeline as Mini Shop, Library domain scenarios."""

    observer_class = LibraryObserver

    # -- helpers ---------------------------------------------------------

    def _create_book(self, headers: dict[str, str], *, deposit: float = 10.0, copies: int = 3) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/api/books",
            headers=headers,
            json_body={
                "title": f"agent-book-{uuid.uuid4().hex[:8]}",
                "author": "agent",
                "deposit": deposit,
                "total_copies": copies,
            },
        )
        response.raise_for_status()
        return response.json()

    def _create_borrow(self, headers: dict[str, str], book_id: int) -> Any:
        return self._request("POST", "/api/borrows", headers=headers, json_body={"book_id": book_id})

    def _book_setup(self, *, deposit: float = 10.0, copies: int = 3):
        user, headers = self._register_and_auth()
        book = self._create_book(headers, deposit=deposit, copies=copies)
        return user, headers, book

    def _assert_borrow_created(self, assertions, before, after, deposit_charged):
        self._assert(
            assertions,
            "borrow_created",
            bool(after["borrow"]) and after["borrow_count"] == before["borrow_count"] + 1,
            before["borrow_count"] + 1,
            after["borrow_count"],
        )
        self._assert(
            assertions,
            "copies_decreased",
            after["book"].get("available_copies") == before["book"].get("available_copies") - 1,
            before["book"].get("available_copies") - 1,
            after["book"].get("available_copies"),
        )
        expected_deposit = round(before["user"].get("deposit") - deposit_charged, 2)
        self._assert(
            assertions,
            "deposit_decreased",
            after["user"].get("deposit") == expected_deposit,
            expected_deposit,
            after["user"].get("deposit"),
        )

    def _assert_no_borrow_side_effect(self, assertions, before, after):
        self._assert(
            assertions,
            "copies_unchanged",
            after["book"].get("available_copies") == before["book"].get("available_copies"),
            before["book"].get("available_copies"),
            after["book"].get("available_copies"),
        )
        self._assert(
            assertions,
            "deposit_unchanged",
            after["user"].get("deposit") == before["user"].get("deposit"),
            before["user"].get("deposit"),
            after["user"].get("deposit"),
        )
        self._assert(
            assertions,
            "borrow_not_created",
            after["borrow_count"] == before["borrow_count"],
            before["borrow_count"],
            after["borrow_count"],
        )

    # -- main-case scenarios ----------------------------------------------

    def _scenario_list_books(self, case, assertions):
        return self._simple(case, assertions, "GET", "/api/books")

    def _scenario_create_book(self, case, assertions):
        _, headers = self._register_and_auth()
        body = {"title": f"agent-book-{uuid.uuid4().hex[:8]}", "author": "agent", "deposit": 10.0, "total_copies": 3}
        before = {"count": self.db.table_count("books")}
        response = self._request("POST", "/api/books", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        book_id = response.json().get("id") if response.ok else None
        after_book = self.db.book(book_id) if book_id else {}
        self._assert(assertions, "book_created", bool(after_book), True, bool(after_book))
        after = {"count": self.db.table_count("books")}
        self._assert(assertions, "book_count_increased", after["count"] == before["count"] + 1, before["count"] + 1, after["count"])
        return _request_view("POST", "/api/books", {"json_body": body}), _response_view(response), {"count": before["count"]}, {"book": after_book, "count": after["count"]}

    def _scenario_get_book(self, case, assertions):
        _, headers, book = self._book_setup()
        return self._simple(case, assertions, "GET", f"/api/books/{book['id']}")

    def _scenario_update_book(self, case, assertions):
        _, headers, book = self._book_setup()
        before = {"book": self.db.book(book["id"])}
        body = {"deposit": 12.5, "total_copies": 7}
        response = self._request("PUT", f"/api/books/{book['id']}", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        after = {"book": self.db.book(book["id"])}
        changed = after["book"].get("deposit") == 12.5 and after["book"].get("total_copies") == 7
        self._assert(assertions, "book_updated", changed, body, after["book"])
        return _request_view("PUT", f"/api/books/{book['id']}", {"json_body": body}), _response_view(response), before, after

    def _scenario_offline_book(self, case, assertions):
        _, headers, book = self._book_setup()
        before = {"book": self.db.book(book["id"])}
        response = self._request("DELETE", f"/api/books/{book['id']}", headers=headers)
        self._check_http(case, response, assertions)
        after = {"book": self.db.book(book["id"])}
        self._assert(
            assertions,
            "book_off_shelf",
            after["book"].get("status") == "off_shelf",
            "off_shelf",
            after["book"].get("status"),
        )
        return _request_view("DELETE", f"/api/books/{book['id']}", {}), _response_view(response), before, after

    def _scenario_create_borrow(self, case, assertions):
        user, headers, book = self._book_setup()
        before = {
            "user": self.db.user(user["id"]),
            "book": self.db.book(book["id"]),
            "borrow_count": self.db.borrow_count(),
        }
        response = self._create_borrow(headers, book["id"])
        self._check_http(case, response, assertions)
        borrow_id = response.json().get("id") if response.ok else None
        after = {
            "user": self.db.user(user["id"]),
            "book": self.db.book(book["id"]),
            "borrow": self.db.borrow(borrow_id),
            "borrow_count": self.db.borrow_count(),
        }
        self._assert_borrow_created(assertions, before, after, book["deposit"])
        return (
            _request_view("POST", "/api/borrows", {"json_body": {"book_id": book["id"]}}),
            _response_view(response),
            before,
            after,
        )

    def _scenario_list_borrows(self, case, assertions):
        _, headers, book = self._book_setup()
        created = self._create_borrow(headers, book["id"])
        created.raise_for_status()
        result = self._simple(case, assertions, "GET", "/api/borrows", headers=headers)
        ids = [item["id"] for item in result[1].get("json", {}).get("items", [])]
        self._assert(assertions, "borrow_in_list", created.json()["id"] in ids, created.json()["id"], ids)
        return result

    def _scenario_return_borrow(self, case, assertions):
        user, headers, book = self._book_setup()
        created = self._create_borrow(headers, book["id"])
        created.raise_for_status()
        borrow_id = created.json()["id"]
        before = {
            "user": self.db.user(user["id"]),
            "book": self.db.book(book["id"]),
            "borrow": self.db.borrow(borrow_id),
        }
        response = self._request("POST", f"/api/borrows/{borrow_id}/return", headers=headers)
        self._check_http(case, response, assertions)
        after = {
            "user": self.db.user(user["id"]),
            "book": self.db.book(book["id"]),
            "borrow": self.db.borrow(borrow_id),
        }
        self._assert(
            assertions,
            "borrow_status_returned",
            after["borrow"].get("status") == "returned",
            "returned",
            after["borrow"].get("status"),
        )
        self._assert(
            assertions,
            "copies_restored",
            after["book"].get("available_copies") == before["book"].get("available_copies") + 1,
            before["book"].get("available_copies") + 1,
            after["book"].get("available_copies"),
        )
        expected_deposit = round(before["user"].get("deposit") + created.json()["deposit_charged"], 2)
        self._assert(
            assertions,
            "deposit_restored",
            after["user"].get("deposit") == expected_deposit,
            expected_deposit,
            after["user"].get("deposit"),
        )
        return (
            _request_view("POST", f"/api/borrows/{borrow_id}/return", {}),
            _response_view(response),
            before,
            after,
        )

    # -- negative scenarios ------------------------------------------------

    def _scenario_create_book_missing_token(self, case, assertions):
        body = {"title": f"agent-book-{uuid.uuid4().hex[:8]}", "author": "agent", "deposit": 10.0, "total_copies": 3}
        before = {"count": self.db.table_count("books")}
        response = self._request("POST", "/api/books", json_body=body)
        self._check_http(case, response, assertions)
        after = {"count": self.db.table_count("books")}
        self._assert(assertions, "book_not_created", before["count"] == after["count"], before["count"], after["count"])
        return _request_view("POST", "/api/books", {"json_body": body}), _response_view(response), before, after

    def _scenario_get_book_not_found(self, case, assertions):
        return self._simple(case, assertions, "GET", "/api/books/999999")

    def _scenario_list_books_invalid_pagination(self, case, assertions):
        statuses = []
        for params in ({"page": 0}, {"size": 101}):
            statuses.append(self._request("GET", "/api/books", params=params).status_code)
        self._assert(assertions, "pagination_boundaries", statuses == [422, 422], [422, 422], statuses)
        return {"method": "GET", "path": "/api/books", "params": "invalid boundaries"}, {"status_codes": statuses}, {}, {}

    def _scenario_borrow_book_not_found(self, case, assertions):
        user, headers = self._register_and_auth()
        before = {"user": self.db.user(user["id"]), "borrow_count": self.db.borrow_count()}
        response = self._create_borrow(headers, 999999)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "borrow_count": self.db.borrow_count()}
        self._assert_no_borrow_side_effect(assertions, {"user": before["user"], "book": {}, "borrow_count": before["borrow_count"]}, {"user": after["user"], "book": {}, "borrow_count": after["borrow_count"]})
        return _request_view("POST", "/api/borrows", {"json_body": {"book_id": 999999}}), _response_view(response), before, after

    def _scenario_borrow_off_shelf(self, case, assertions):
        user, headers, book = self._book_setup()
        self._request("DELETE", f"/api/books/{book['id']}", headers=headers).raise_for_status()
        before = {"user": self.db.user(user["id"]), "book": self.db.book(book["id"]), "borrow_count": self.db.borrow_count()}
        response = self._create_borrow(headers, book["id"])
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "book": self.db.book(book["id"]), "borrow_count": self.db.borrow_count()}
        self._assert(assertions, "copies_unchanged", after["book"].get("available_copies") == before["book"].get("available_copies"), before["book"].get("available_copies"), after["book"].get("available_copies"))
        self._assert(assertions, "deposit_unchanged", after["user"].get("deposit") == before["user"].get("deposit"), before["user"].get("deposit"), after["user"].get("deposit"))
        self._assert(assertions, "borrow_not_created", after["borrow_count"] == before["borrow_count"], before["borrow_count"], after["borrow_count"])
        return _request_view("POST", "/api/borrows", {"json_body": {"book_id": book["id"]}}), _response_view(response), before, after

    def _failed_borrow(self, case, assertions, user, headers, book):
        before = {"user": self.db.user(user["id"]), "book": self.db.book(book["id"]), "borrow_count": self.db.borrow_count()}
        response = self._create_borrow(headers, book["id"])
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "book": self.db.book(book["id"]), "borrow_count": self.db.borrow_count()}
        self._assert_no_borrow_side_effect(assertions, before, after)
        return _request_view("POST", "/api/borrows", {"json_body": {"book_id": book["id"]}}), _response_view(response), before, after

    def _scenario_insufficient_copies(self, case, assertions):
        # total_copies=1，借走唯一副本后，同用户对同一本书再借必然失败
        user, headers, book = self._book_setup(copies=1)
        first = self._create_borrow(headers, book["id"])
        first.raise_for_status()
        return self._failed_borrow(case, assertions, user, headers, book)

    def _scenario_insufficient_deposit(self, case, assertions):
        # 押金 600 > 默认余额 500，直接借必然失败
        user, headers, book = self._book_setup(deposit=600.0, copies=3)
        return self._failed_borrow(case, assertions, user, headers, book)

    def _scenario_borrow_missing_token(self, case, assertions):
        user, headers, book = self._book_setup()
        before = {"user": self.db.user(user["id"]), "book": self.db.book(book["id"]), "borrow_count": self.db.borrow_count()}
        response = self._request("POST", "/api/borrows", json_body={"book_id": book["id"]})
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "book": self.db.book(book["id"]), "borrow_count": self.db.borrow_count()}
        self._assert(assertions, "copies_unchanged", after["book"].get("available_copies") == before["book"].get("available_copies"), before["book"].get("available_copies"), after["book"].get("available_copies"))
        self._assert(assertions, "deposit_unchanged", after["user"].get("deposit") == before["user"].get("deposit"), before["user"].get("deposit"), after["user"].get("deposit"))
        self._assert(assertions, "borrow_not_created", after["borrow_count"] == before["borrow_count"], before["borrow_count"], after["borrow_count"])
        return _request_view("POST", "/api/borrows", {"json_body": {"book_id": book["id"]}}), _response_view(response), before, after

    def _scenario_return_twice(self, case, assertions):
        _, headers, book = self._book_setup()
        created = self._create_borrow(headers, book["id"])
        created.raise_for_status()
        borrow_id = created.json()["id"]
        self._request("POST", f"/api/borrows/{borrow_id}/return", headers=headers).raise_for_status()
        before = {
            "user": self.db.user(created.json()["user_id"]),
            "book": self.db.book(book["id"]),
            "borrow": self.db.borrow(borrow_id),
        }
        response = self._request("POST", f"/api/borrows/{borrow_id}/return", headers=headers)
        self._check_http(case, response, assertions)
        after = {
            "user": self.db.user(created.json()["user_id"]),
            "book": self.db.book(book["id"]),
            "borrow": self.db.borrow(borrow_id),
        }
        self._assert(assertions, "state_unchanged", before == after, before, after)
        return _request_view("POST", f"/api/borrows/{borrow_id}/return", {}), _response_view(response), before, after

    def _scenario_return_other_user(self, case, assertions):
        _, headers_a, book = self._book_setup()
        created = self._create_borrow(headers_a, book["id"])
        created.raise_for_status()
        _, headers_b = self._register_and_auth()
        before = {"book": self.db.book(book["id"])}
        response = self._request("POST", f"/api/borrows/{created.json()['id']}/return", headers=headers_b)
        self._check_http(case, response, assertions)
        self._assert(assertions, "borrow_hidden", response.status_code == 404, 404, response.status_code)
        after = {"book": self.db.book(book["id"])}
        self._assert(assertions, "state_unchanged", before == after, before, after)
        return _request_view("POST", f"/api/borrows/{created.json()['id']}/return", {}), _response_view(response), before, after
