"""Controlled Mini Shop scenario executor with evidence capture."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests
from jsonschema import ValidationError, validate

from api_agent.agentlog import AgentLogger
from api_agent.artifacts import safe_name, write_model
from api_agent.database import DatabaseObserver
from api_agent.models import AssertionResult, CaseEvidence, NormalizedRequirement, TestCase

LOCAL_TEST_HOSTS = {"127.0.0.1", "localhost"}


class ScenarioExecutor:
    """Mini Shop scenario executor; subclasses swap observer and scenarios."""

    observer_class = DatabaseObserver

    def __init__(
        self,
        base_url: str,
        database_url: str,
        evidence_dir: Path,
        run_id: str,
        requirement: NormalizedRequirement,
        cleanup_spec: Any = None,
        fixed_accounts: bool = False,
        observable_tables: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in LOCAL_TEST_HOSTS:
            raise ValueError("runner only permits a local test target (http://127.0.0.1 or http://localhost)")
        self.db = self.observer_class(database_url)
        self.db.allowed_tables = dict(observable_tables or {})
        self.database_url = database_url
        self.cleanup_spec = cleanup_spec
        self.fixed_accounts = fixed_accounts
        self.evidence_dir = evidence_dir
        self.run_id = run_id
        self.operations = {item.operation_id: item for item in requirement.operations}
        self.session = requests.Session()
        self.logger = AgentLogger(evidence_dir / "agent-log.jsonl", run_id)
        self._last_request_id = ""
        self._case_key = ""
        self._registration_index = 0

    def close(self) -> None:
        self.session.close()
        self.db.close()

    def execute(self, case: TestCase) -> CaseEvidence:
        started = time.perf_counter()
        assertions: list[AssertionResult] = []
        request_evidence: dict[str, Any] = {}
        response_evidence: dict[str, Any] = {}
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        try:
            handler = getattr(self, f"_scenario_{case.scenario}", None)
            if handler is None:
                self._assert(assertions, "scenario_supported", False, True, False, f"Unsupported scenario: {case.scenario}")
            else:
                # 固定账号模式下，用户名由 (用例, 注册序号) 决定，跨运行保持一致
                self._case_key = safe_name(case.case_id)[:13]
                self._registration_index = 0
                request_evidence, response_evidence, before, after = handler(case, assertions)
        except (requests.RequestException, OSError) as exc:
            assertions.append(AssertionResult(name="runtime_available", status="inconclusive", detail=str(exc)))
        except Exception as exc:  # keep unexpected evidence visible to pytest and the report
            assertions.append(AssertionResult(name="scenario_execution", status="failed", detail=f"{type(exc).__name__}: {exc}"))

        statuses = {item.status for item in assertions}
        status = "failed" if "failed" in statuses else "inconclusive" if "inconclusive" in statuses else "passed"
        evidence = CaseEvidence(
            run_id=self.run_id,
            case_id=case.case_id,
            operation_id=case.operation_id,
            status=status,
            request_id=self._last_request_id,
            request=request_evidence,
            response=response_evidence,
            database_before=before,
            database_after=after,
            assertions=assertions,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        self.logger.log(
            "case_evidence",
            case_id=case.case_id,
            operation_id=case.operation_id,
            request_id=self._last_request_id,
            detail={"status": status, "duration_ms": evidence.duration_ms, "assertions": len(assertions)},
        )
        write_model(self.evidence_dir / f"{safe_name(case.case_id)}.json", evidence)
        return evidence

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        self._last_request_id = f"{self.run_id}-{uuid.uuid4().hex[:8]}"
        safe_headers = {**(headers or {}), "X-Request-ID": self._last_request_id}
        response = self.session.request(
            method,
            self.base_url + path,
            headers=safe_headers,
            json=json_body,
            params=params,
            timeout=5,
        )
        self.logger.log(
            "http_request",
            request_id=self._last_request_id,
            detail={"method": method, "path": path, "status_code": response.status_code},
        )
        return response

    def _register_and_auth(self) -> tuple[dict[str, Any], dict[str, str]]:
        username = self._next_username()
        credentials = {"username": username, "password": "Test123456"}
        registered = self._request("POST", "/api/auth/register", json_body=credentials)
        if registered.status_code == 409 and self.fixed_accounts:
            # 上次运行残留的同名账号：交给确定性清理工具先查后删，再重试一次
            if self._recover_fixed_account(username):
                registered = self._request("POST", "/api/auth/register", json_body=credentials)
        if registered.status_code == 409:
            # 真实性约束兜底：清理不可用时换全新账号继续，不中断整轮运行
            username = f"agent_{uuid.uuid4().hex[:12]}"
            credentials = {"username": username, "password": "Test123456"}
            registered = self._request("POST", "/api/auth/register", json_body=credentials)
        registered.raise_for_status()
        login = self._request("POST", "/api/auth/login", json_body=credentials)
        login.raise_for_status()
        return registered.json(), {"Authorization": f"Bearer {login.json()['access_token']}"}

    def _next_username(self) -> str:
        if not self.fixed_accounts:
            return f"agent_{uuid.uuid4().hex[:12]}"
        self._registration_index += 1
        # agent_(6) + case_key(13) + _(1) + 单位序号(1) = 21 → key 截到 12 保证 ≤20
        return f"agent_{self._case_key[:12]}_{self._registration_index}"

    def _recover_fixed_account(self, username: str) -> bool:
        if not self.cleanup_spec:
            self.logger.log(
                "cleanup_account",
                agent="executor",
                level="warning",
                detail={"username": username, "deleted": False, "reason": "no_cleanup_spec"},
            )
            return False
        from api_agent.cleanup import CleanupTool

        tool = CleanupTool(self.database_url, self.cleanup_spec)
        try:
            result = tool.cleanup_username(username)
        finally:
            tool.close()
        self.logger.log("cleanup_account", agent="executor", detail=result)
        return bool(result.get("deleted"))

    def _create_product(self, headers: dict[str, str], *, price: float = 10.0, stock: int = 5) -> dict[str, Any]:
        response = self._request(
            "POST", "/api/products", headers=headers,
            json_body={"name": f"agent-product-{uuid.uuid4().hex[:8]}", "price": price, "stock": stock},
        )
        response.raise_for_status()
        return response.json()

    def _create_order(self, headers: dict[str, str], product_id: int, quantity: int = 2) -> requests.Response:
        return self._request("POST", "/api/orders", headers=headers, json_body={"product_id": product_id, "quantity": quantity})

    def _create_coupon(self, headers: dict[str, str], *, code: str | None = None, discount_percent: float = 10, max_uses: int = 2) -> dict[str, Any]:
        code = code or f"SAVE_{uuid.uuid4().hex[:8].upper()}"
        response = self._request("POST", "/api/coupons", headers=headers, json_body={"code": code, "discount_percent": discount_percent, "max_uses": max_uses})
        response.raise_for_status()
        return response.json()

    def _check_http(self, case: TestCase, response: requests.Response, assertions: list[AssertionResult]) -> None:
        self._assert(assertions, "http_status", response.status_code in case.expected_status_codes, case.expected_status_codes, response.status_code)
        operation = self.operations.get(case.operation_id)
        schema = operation.responses.get(str(response.status_code)) if operation else None
        if schema:
            try:
                validate(response.json(), schema)
                assertions.append(AssertionResult(name="response_schema", status="passed", expected="OpenAPI schema", actual="valid"))
            except (ValidationError, ValueError) as exc:
                assertions.append(AssertionResult(name="response_schema", status="failed", expected="OpenAPI schema", actual="invalid", detail=str(exc)))

    def _simple(self, case: TestCase, assertions: list[AssertionResult], method: str, path: str, **kwargs):
        response = self._request(method, path, **kwargs)
        self._check_http(case, response, assertions)
        return _request_view(method, path, kwargs), _response_view(response), {}, {}

    def _scenario_health_check(self, case, assertions):
        return self._simple(case, assertions, "GET", "/health")

    def _scenario_register(self, case, assertions):
        body = {"username": f"agent_{uuid.uuid4().hex[:12]}", "password": "Test123456"}
        return self._simple(case, assertions, "POST", "/api/auth/register", json_body=body)

    def _scenario_login(self, case, assertions):
        credentials = {"username": f"agent_{uuid.uuid4().hex[:12]}", "password": "Test123456"}
        self._request("POST", "/api/auth/register", json_body=credentials).raise_for_status()
        result = self._simple(case, assertions, "POST", "/api/auth/login", json_body=credentials)
        response_data = result[1].get("json", {})
        self._assert(assertions, "token_present", bool(response_data.get("access_token")), True, bool(response_data.get("access_token")))
        return result

    def _scenario_wrong_password(self, case, assertions):
        credentials, _ = self._register_and_auth()
        body = {"username": credentials["username"], "password": "wrong_password999"}
        return self._simple(case, assertions, "POST", "/api/auth/login", json_body=body)

    def _scenario_missing_token(self, case, assertions):
        return self._simple(case, assertions, "GET", "/api/auth/me")

    def _scenario_register_boundaries(self, case, assertions):
        responses = []
        suffix = uuid.uuid4().hex[:12]
        # 用户名边界：2 字符拒绝、3–20 合法、21 拒绝；名字必须每次唯一，否则修复重跑会撞 409
        for username in ["ab", f"agent_{suffix}", "u" * 10 + suffix[:10], "u" * 10 + suffix[:11]]:
            response = self._request("POST", "/api/auth/register", json_body={"username": username, "password": "Test123456"})
            responses.append(response.status_code)
        self._assert(assertions, "boundary_statuses", responses == [422, 201, 201, 422], [422, 201, 201, 422], responses)
        self._assert(assertions, "invalid_not_created", responses.count(422) == 2, 2, responses.count(422))
        return {"method": "POST", "path": "/api/auth/register", "json": "boundary cases"}, {"status_codes": responses}, {}, {}

    def _scenario_duplicate_register(self, case, assertions):
        body = {"username": f"agent_{uuid.uuid4().hex[:12]}", "password": "Test123456"}
        first = self._request("POST", "/api/auth/register", json_body=body)
        second = self._request("POST", "/api/auth/register", json_body=body)
        self._assert(assertions, "http_status", second.status_code == 409, 409, second.status_code)
        self._assert(assertions, "duplicate_not_created", first.status_code == 201 and second.status_code == 409, "201 then 409", f"{first.status_code} then {second.status_code}")
        return _request_view("POST", "/api/auth/register", {"json_body": body}), _response_view(second), {}, {}

    def _scenario_current_user(self, case, assertions):
        user, headers = self._register_and_auth()
        result = self._simple(case, assertions, "GET", "/api/auth/me", headers=headers)
        self._assert(assertions, "authenticated_user", result[1].get("json", {}).get("id") == user["id"], user["id"], result[1].get("json", {}).get("id"))
        return result

    def _scenario_list_products(self, case, assertions):
        return self._simple(case, assertions, "GET", "/api/products")

    def _scenario_create_product(self, case, assertions):
        _, headers = self._register_and_auth()
        body = {"name": f"agent-product-{uuid.uuid4().hex[:8]}", "price": 10.0, "stock": 5}
        response = self._request("POST", "/api/products", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        product_id = response.json().get("id") if response.ok else None
        after = {"product": self.db.product(product_id)} if product_id else {}
        self._assert(assertions, "product_created", bool(after.get("product")), True, bool(after.get("product")))
        return _request_view("POST", "/api/products", {"json_body": body}), _response_view(response), {}, after

    def _scenario_create_product_missing_token(self, case, assertions):
        body = {"name": f"agent-product-{uuid.uuid4().hex[:8]}", "price": 10.0, "stock": 5}
        before = {"count": self._table_count("products")}
        response = self._request("POST", "/api/products", json_body=body)
        self._check_http(case, response, assertions)
        after = {"count": self._table_count("products")}
        self._assert(assertions, "product_not_created", before["count"] == after["count"], before["count"], after["count"])
        return _request_view("POST", "/api/products", {"json_body": body}), _response_view(response), before, after

    def _scenario_create_product_invalid_price(self, case, assertions):
        _, headers = self._register_and_auth()
        body = {"name": f"agent-product-{uuid.uuid4().hex[:8]}", "price": 0, "stock": 5}
        before = {"count": self._table_count("products")}
        response = self._request("POST", "/api/products", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        after = {"count": self._table_count("products")}
        self._assert(assertions, "product_not_created", before["count"] == after["count"], before["count"], after["count"])
        return _request_view("POST", "/api/products", {"json_body": body}), _response_view(response), before, after

    def _scenario_get_product(self, case, assertions):
        _, headers = self._register_and_auth()
        product = self._create_product(headers)
        return self._simple(case, assertions, "GET", f"/api/products/{product['id']}")

    def _scenario_get_product_not_found(self, case, assertions):
        return self._simple(case, assertions, "GET", "/api/products/999999")

    def _scenario_list_products_invalid_pagination(self, case, assertions):
        statuses = []
        for params in ({"page": -1}, {"size": 1000}):
            statuses.append(self._request("GET", "/api/products", params=params).status_code)
        self._assert(assertions, "pagination_boundaries", statuses == [422, 422], [422, 422], statuses)
        return {"method": "GET", "path": "/api/products", "params": "invalid boundaries"}, {"status_codes": statuses}, {}, {}

    def _scenario_update_product(self, case, assertions):
        _, headers = self._register_and_auth()
        product = self._create_product(headers)
        before = {"product": self.db.product(product["id"])}
        body = {"price": 12.5, "stock": 7}
        response = self._request("PUT", f"/api/products/{product['id']}", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        after = {"product": self.db.product(product["id"])}
        changed = after["product"].get("price") == 12.5 and after["product"].get("stock") == 7
        self._assert(assertions, "product_updated", changed, body, after["product"])
        return _request_view("PUT", f"/api/products/{product['id']}", {"json_body": body}), _response_view(response), before, after

    def _scenario_offline_product(self, case, assertions):
        _, headers = self._register_and_auth()
        product = self._create_product(headers)
        before = {"product": self.db.product(product["id"])}
        response = self._request("DELETE", f"/api/products/{product['id']}", headers=headers)
        self._check_http(case, response, assertions)
        after = {"product": self.db.product(product["id"])}
        self._assert(assertions, "product_off_sale", after["product"].get("status") == "off_sale", "off_sale", after["product"].get("status"))
        return _request_view("DELETE", f"/api/products/{product['id']}", {}), _response_view(response), before, after

    def _order_setup(self, *, price: float = 10.0, stock: int = 5):
        user, headers = self._register_and_auth()
        product = self._create_product(headers, price=price, stock=stock)
        return user, headers, product

    def _scenario_create_order(self, case, assertions):
        user, headers, product = self._order_setup()
        before = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"]), "order_count": self.db.order_count()}
        response = self._create_order(headers, product["id"], 2)
        self._check_http(case, response, assertions)
        order_id = response.json().get("id") if response.ok else None
        after = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"]), "order": self.db.order(order_id), "order_count": self.db.order_count()}
        self._assert_order_created(assertions, before, after, quantity=2, amount=20.0)
        return _request_view("POST", "/api/orders", {"json_body": {"product_id": product["id"], "quantity": 2}}), _response_view(response), before, after

    def _scenario_list_orders(self, case, assertions):
        _, headers, product = self._order_setup()
        created = self._create_order(headers, product["id"], 1)
        created.raise_for_status()
        result = self._simple(case, assertions, "GET", "/api/orders", headers=headers)
        ids = [item["id"] for item in result[1].get("json", {}).get("items", [])]
        self._assert(assertions, "order_in_list", created.json()["id"] in ids, created.json()["id"], ids)
        return result

    def _scenario_get_order(self, case, assertions):
        _, headers, product = self._order_setup()
        created = self._create_order(headers, product["id"], 1)
        created.raise_for_status()
        result = self._simple(case, assertions, "GET", f"/api/orders/{created.json()['id']}", headers=headers)
        self._assert(assertions, "order_matches", result[1].get("json", {}).get("id") == created.json()["id"], created.json()["id"], result[1].get("json", {}).get("id"))
        return result

    def _scenario_pay_order(self, case, assertions):
        _, headers, product = self._order_setup()
        created = self._create_order(headers, product["id"], 1)
        created.raise_for_status()
        order_id = created.json()["id"]
        before = {"order": self.db.order(order_id)}
        response = self._request("POST", f"/api/orders/{order_id}/pay", headers=headers)
        self._check_http(case, response, assertions)
        after = {"order": self.db.order(order_id)}
        self._assert(assertions, "order_status_paid", after["order"].get("status") == "paid", "paid", after["order"].get("status"))
        return _request_view("POST", f"/api/orders/{order_id}/pay", {}), _response_view(response), before, after

    def _scenario_cancel_order(self, case, assertions):
        user, headers, product = self._order_setup()
        initial = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"])}
        created = self._create_order(headers, product["id"], 2)
        created.raise_for_status()
        order_id = created.json()["id"]
        before = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"]), "order": self.db.order(order_id), "initial": initial}
        response = self._request("POST", f"/api/orders/{order_id}/cancel", headers=headers)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"]), "order": self.db.order(order_id)}
        self._assert(assertions, "order_status_cancelled", after["order"].get("status") == "cancelled", "cancelled", after["order"].get("status"))
        self._assert(assertions, "inventory_restored", after["product"].get("stock") == initial["product"].get("stock"), initial["product"].get("stock"), after["product"].get("stock"))
        self._assert(assertions, "balance_restored", after["user"].get("balance") == initial["user"].get("balance"), initial["user"].get("balance"), after["user"].get("balance"))
        return _request_view("POST", f"/api/orders/{order_id}/cancel", {}), _response_view(response), before, after

    def _scenario_insufficient_stock(self, case, assertions):
        user, headers, product = self._order_setup(stock=1)
        return self._failed_order(case, assertions, user, headers, product, quantity=2)

    def _scenario_insufficient_balance(self, case, assertions):
        user, headers, product = self._order_setup(price=2000.0, stock=1)
        return self._failed_order(case, assertions, user, headers, product, quantity=1)

    def _failed_order(self, case, assertions, user, headers, product, quantity):
        before = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"]), "order_count": self.db.order_count()}
        response = self._create_order(headers, product["id"], quantity)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"]), "order_count": self.db.order_count()}
        self._assert(assertions, "inventory_unchanged", after["product"].get("stock") == before["product"].get("stock"), before["product"].get("stock"), after["product"].get("stock"))
        self._assert(assertions, "balance_unchanged", after["user"].get("balance") == before["user"].get("balance"), before["user"].get("balance"), after["user"].get("balance"))
        self._assert(assertions, "order_not_created", after["order_count"] == before["order_count"], before["order_count"], after["order_count"])
        return _request_view("POST", "/api/orders", {"json_body": {"product_id": product["id"], "quantity": quantity}}), _response_view(response), before, after

    def _scenario_pay_twice(self, case, assertions):
        _, headers, product = self._order_setup()
        created = self._create_order(headers, product["id"], 1)
        created.raise_for_status()
        order_id = created.json()["id"]
        first = self._request("POST", f"/api/orders/{order_id}/pay", headers=headers)
        first.raise_for_status()
        before = {"order": self.db.order(order_id)}
        response = self._request("POST", f"/api/orders/{order_id}/pay", headers=headers)
        self._check_http(case, response, assertions)
        after = {"order": self.db.order(order_id)}
        self._assert(assertions, "order_status_paid", after["order"].get("status") == "paid", "paid", after["order"].get("status"))
        return _request_view("POST", f"/api/orders/{order_id}/pay", {}), _response_view(response), before, after

    def _scenario_order_product_not_found(self, case, assertions):
        user, headers = self._register_and_auth()
        before = {"user": self.db.user(user["id"]), "order_count": self.db.order_count()}
        response = self._create_order(headers, 999999, 1)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "order_count": self.db.order_count()}
        self._assert(assertions, "order_not_created", before["order_count"] == after["order_count"], before["order_count"], after["order_count"])
        return _request_view("POST", "/api/orders", {"json_body": {"product_id": 999999, "quantity": 1}}), _response_view(response), before, after

    def _scenario_order_invalid_quantity(self, case, assertions):
        user, headers, product = self._order_setup()
        before = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"]), "order_count": self.db.order_count()}
        response = self._create_order(headers, product["id"], 0)
        self._check_http(case, response, assertions)
        after = {"user": self.db.user(user["id"]), "product": self.db.product(product["id"]), "order_count": self.db.order_count()}
        self._assert(assertions, "order_not_created", before["order_count"] == after["order_count"], before["order_count"], after["order_count"])
        return _request_view("POST", "/api/orders", {"json_body": {"product_id": product["id"], "quantity": 0}}), _response_view(response), before, after

    def _scenario_cancel_paid(self, case, assertions):
        _, headers, product = self._order_setup()
        created = self._create_order(headers, product["id"], 1)
        created.raise_for_status()
        order_id = created.json()["id"]
        self._request("POST", f"/api/orders/{order_id}/pay", headers=headers).raise_for_status()
        before = {"order": self.db.order(order_id), "product": self.db.product(product["id"])}
        response = self._request("POST", f"/api/orders/{order_id}/cancel", headers=headers)
        self._check_http(case, response, assertions)
        after = {"order": self.db.order(order_id), "product": self.db.product(product["id"])}
        self._assert(assertions, "state_unchanged", before == after, before, after)
        return _request_view("POST", f"/api/orders/{order_id}/cancel", {}), _response_view(response), before, after

    def _scenario_cancel_cancelled(self, case, assertions):
        _, headers, product = self._order_setup()
        created = self._create_order(headers, product["id"], 1)
        created.raise_for_status()
        order_id = created.json()["id"]
        self._request("POST", f"/api/orders/{order_id}/cancel", headers=headers).raise_for_status()
        before = {"order": self.db.order(order_id), "product": self.db.product(product["id"])}
        response = self._request("POST", f"/api/orders/{order_id}/cancel", headers=headers)
        self._check_http(case, response, assertions)
        after = {"order": self.db.order(order_id), "product": self.db.product(product["id"])}
        self._assert(assertions, "state_unchanged", before == after, before, after)
        return _request_view("POST", f"/api/orders/{order_id}/cancel", {}), _response_view(response), before, after

    def _scenario_other_user_order(self, case, assertions):
        _, headers_a, product = self._order_setup()
        created = self._create_order(headers_a, product["id"], 1)
        created.raise_for_status()
        user_b, headers_b = self._register_and_auth()
        response = self._request("GET", f"/api/orders/{created.json()['id']}", headers=headers_b)
        self._check_http(case, response, assertions)
        self._assert(assertions, "order_hidden", response.status_code == 404, 404, response.status_code)
        return _request_view("GET", f"/api/orders/{created.json()['id']}", {}), _response_view(response), {}, {}

    def _cart_setup(self):
        user, headers = self._register_and_auth()
        product = self._create_product(headers, stock=10)
        return user, headers, product

    def _scenario_get_cart(self, case, assertions):
        user, headers, product = self._cart_setup()
        self._request("POST", "/api/cart/items", headers=headers, json_body={"product_id": product["id"], "quantity": 2}).raise_for_status()
        before = {"cart_items": self.db.cart_items(user["id"])}
        response = self._request("GET", "/api/cart", headers=headers)
        self._check_http(case, response, assertions)
        after = {"cart_items": self.db.cart_items(user["id"])}
        items = response.json().get("items", []) if response.ok else []
        self._assert(assertions, "cart_user_isolation", len(items) == 1 and items[0].get("product_id") == product["id"], product["id"], items)
        return _request_view("GET", "/api/cart", {}), _response_view(response), before, after

    def _scenario_add_cart_item(self, case, assertions):
        user, headers, product = self._cart_setup()
        before = {"cart_items": self.db.cart_items(user["id"])}
        body = {"product_id": product["id"], "quantity": 2}
        response = self._request("POST", "/api/cart/items", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        after = {"cart_items": self.db.cart_items(user["id"])}
        self._assert(assertions, "cart_item_added", len(after["cart_items"]) == 1 and after["cart_items"][0]["quantity"] == 2, 2, after["cart_items"])
        self._assert(assertions, "stock_limit", after["cart_items"][0]["quantity"] <= self.db.product(product["id"])["stock"], True, after["cart_items"][0]["quantity"] <= self.db.product(product["id"])["stock"])
        return _request_view("POST", "/api/cart/items", {"json_body": body}), _response_view(response), before, after

    def _scenario_update_cart_item(self, case, assertions):
        user, headers, product = self._cart_setup()
        self._request("POST", "/api/cart/items", headers=headers, json_body={"product_id": product["id"], "quantity": 1}).raise_for_status()
        before = {"cart_items": self.db.cart_items(user["id"])}
        body = {"product_id": product["id"], "quantity": 3}
        response = self._request("PUT", f"/api/cart/items/{product['id']}", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        after = {"cart_items": self.db.cart_items(user["id"])}
        self._assert(assertions, "cart_quantity_updated", after["cart_items"][0]["quantity"] == 3, 3, after["cart_items"])
        return _request_view("PUT", f"/api/cart/items/{product['id']}", {"json_body": body}), _response_view(response), before, after

    def _scenario_remove_cart_item(self, case, assertions):
        user, headers, product = self._cart_setup()
        self._request("POST", "/api/cart/items", headers=headers, json_body={"product_id": product["id"], "quantity": 1}).raise_for_status()
        before = {"cart_items": self.db.cart_items(user["id"])}
        response = self._request("DELETE", f"/api/cart/items/{product['id']}", headers=headers)
        self._check_http(case, response, assertions)
        after = {"cart_items": self.db.cart_items(user["id"])}
        self._assert(assertions, "cart_item_removed", not after["cart_items"], [], after["cart_items"])
        return _request_view("DELETE", f"/api/cart/items/{product['id']}", {}), _response_view(response), before, after

    def _scenario_clear_cart(self, case, assertions):
        user, headers, product = self._cart_setup()
        self._request("POST", "/api/cart/items", headers=headers, json_body={"product_id": product["id"], "quantity": 1}).raise_for_status()
        before = {"cart_items": self.db.cart_items(user["id"])}
        response = self._request("DELETE", "/api/cart", headers=headers)
        self._check_http(case, response, assertions)
        after = {"cart_items": self.db.cart_items(user["id"])}
        self._assert(assertions, "cart_cleared", not after["cart_items"], [], after["cart_items"])
        return _request_view("DELETE", "/api/cart", {}), _response_view(response), before, after

    def _scenario_create_coupon(self, case, assertions):
        user, headers = self._register_and_auth()
        code = f"SAVE_{uuid.uuid4().hex[:8].upper()}"
        before = {"coupon": self.db.coupon(code)}
        body = {"code": code, "discount_percent": 10, "max_uses": 2}
        response = self._request("POST", "/api/coupons", headers=headers, json_body=body)
        self._check_http(case, response, assertions)
        after = {"coupon": self.db.coupon(code)}
        self._assert(assertions, "coupon_created", bool(after["coupon"]) and after["coupon"]["code"] == code, code, after["coupon"])
        return _request_view("POST", "/api/coupons", {"json_body": body}), _response_view(response), before, after

    def _scenario_list_coupons(self, case, assertions):
        _, headers = self._register_and_auth()
        self._create_coupon(headers)
        return self._simple(case, assertions, "GET", "/api/coupons", headers=headers)

    def _scenario_inventory_transactions(self, case, assertions):
        user, headers, product = self._order_setup(stock=10)
        before = {"inventory_transactions": self.db.inventory_transactions(product["id"]), "product": self.db.product(product["id"])}
        created = self._create_order(headers, product["id"], 2)
        created.raise_for_status()
        order_id = created.json()["id"]
        cancelled = self._request("POST", f"/api/orders/{order_id}/cancel", headers=headers)
        cancelled.raise_for_status()
        response = self._request("GET", f"/api/inventory/{product['id']}/transactions", headers=headers)
        self._check_http(case, response, assertions)
        after = {"inventory_transactions": self.db.inventory_transactions(product["id"]), "product": self.db.product(product["id"])}
        entries = after["inventory_transactions"]
        self._assert(assertions, "inventory_evidence", len(entries) >= 2 and entries[-2]["quantity_change"] == -2 and entries[-1]["quantity_change"] == 2, "-2 then +2", entries)
        return _request_view("GET", f"/api/inventory/{product['id']}/transactions", {}), _response_view(response), before, after

    # -- generic contract smoke ------------------------------------------

    def _scenario_generic(self, case, assertions):
        """Schema-driven smoke for operations without a hand-written scenario.

        请求由 OpenAPI schema 确定性构造（必填字段、长度、枚举、最小值），
        需要认证则先注册登录。状态码判定：拒绝 5xx/3xx/未声明状态码，
        容忍数据依赖的 4xx（FastAPI 不会把 HTTPException 的 401/404 写进
        OpenAPI，通用请求命中它们属于正常数据依赖）；2xx 响应必须通过
        契约结构校验。语义级回归由预写业务场景负责。
        """
        operation = self.operations.get(case.operation_id)
        if operation is None:
            self._assert(assertions, "operation_present", False, True, False, "operation spec missing")
            return _request_view("GET", "", {}), _response_view_of_none(), {}, {}

        path = operation.path
        for param in operation.parameters:
            if param.location == "path":
                path = path.replace("{" + param.name + "}", str(_schema_value(param.schema_)))
        query = {
            param.name: _schema_value(param.schema_)
            for param in operation.parameters
            if param.location == "query" and param.required
        }
        headers = None
        if operation.security_required:
            _, headers = self._register_and_auth()
        body = _body_from_schema(operation.request_schema)
        response = self._request(operation.method, path, headers=headers, json_body=body, params=query or None)

        documented = set(case.expected_status_codes)
        status = response.status_code
        accepted = status in documented or 400 <= status < 500
        self._assert(
            assertions,
            "http_status",
            accepted,
            sorted(documented) or "2xx",
            status,
            "" if accepted else "5xx/3xx/undocumented status from a schema-valid request",
        )
        schema = operation.responses.get(str(status))
        if schema and 200 <= status < 300:
            try:
                validate(response.json(), schema)
                assertions.append(AssertionResult(name="response_schema", status="passed", expected="OpenAPI schema", actual="valid"))
            except (ValidationError, ValueError) as exc:
                assertions.append(AssertionResult(name="response_schema", status="failed", expected="OpenAPI schema", actual="invalid", detail=str(exc)))
        return (
            _request_view(operation.method, path, {"json_body": body, "params": query}),
            _response_view(response),
            {},
            {},
        )

    # -- LLM rule cases (declarative DSL, deterministic execution) -------

    def _load_llm_rules(self) -> dict[str, Any]:
        rules_path = os.environ.get("API_AGENT_RULES_PATH")
        if not rules_path or not Path(rules_path).exists():
            return {}
        return json.loads(Path(rules_path).read_text(encoding="utf-8"))

    def _scenario_llm(self, case, assertions):
        """Execute one LLM-extracted rule: setup primitives, action, DSL assertions.

        规则来自 llm-rules.json（经 Pydantic 校验）；LLM 从不直接触库，
        断言全部由 DbAssertionEngine 的白名单查询执行。
        """
        from api_agent.db_assert import DbAssertionEngine, resolve_placeholders
        from api_agent.llm_rules import LLMDBAssertion

        rule_id = case.case_id.removeprefix("llm.")
        rules_doc = self._load_llm_rules()
        rule = next((item for item in rules_doc.get("rules", []) if item.get("rule_id") == rule_id), None)
        if rule is None:
            self._assert(assertions, "rule_present", False, True, False, f"rule {rule_id} not found in llm-rules.json")
            return _request_view("GET", "", {}), _response_view_of_none(), {}, {}

        method = rule.get("action_method") or rule.get("interface", "POST").split(" ", 1)[0] or "POST"
        resources: dict[str, Any] = {}
        token_headers: dict[str, str] = {}
        try:
            for step in rule.get("setup", []):
                if step.get("action") == "register":
                    user, token_headers = self._register_and_auth()
                    resources["user"] = user
                    resources[step.get("resource") or "user"] = user
                elif step.get("action") == "create":
                    body = resolve_placeholders(step.get("overrides") or {}, resources)
                    response = self._request(step.get("method", "POST"), step.get("path", ""), headers=token_headers, json_body=body or None)
                    response.raise_for_status()
                    resources[step.get("resource") or "created"] = response.json()

            db_assertions = [LLMDBAssertion.model_validate(item) for item in rule.get("db_assertions", [])]
            engine = DbAssertionEngine(db_assertions, resources, self.db)
            before_states = engine.capture_before()

            action_path = resolve_placeholders(rule.get("action_path", ""), resources)
            action_body = resolve_placeholders(rule.get("action_body", {}) or {}, resources)
            response = self._request(method, action_path, headers=token_headers, json_body=action_body or None)
            self._assert(
                assertions,
                "http_status",
                response.status_code in case.expected_status_codes,
                case.expected_status_codes,
                response.status_code,
            )
            resources["result"] = response.json() if response.ok else {}
            assertions.extend(engine.evaluate(before_states))
            return (
                _request_view(method, action_path, {"json_body": action_body}),
                _response_view(response),
                {},
                {},
            )
        except (requests.RequestException, OSError) as exc:
            self._assert(assertions, "runtime_available", False, True, False, str(exc))
            return _request_view("GET", "", {}), _response_view_of_none(), {}, {}

    def _table_count(self, table: str) -> int:
        if table != "products":
            raise ValueError("unsupported table")
        from sqlalchemy import text
        with self.db._engine.connect() as connection:
            return int(connection.execute(text("SELECT COUNT(*) FROM products")).scalar_one())

    def _assert_order_created(self, assertions, before, after, quantity, amount):
        order = after["order"]
        self._assert(assertions, "order_created", bool(order) and after["order_count"] == before["order_count"] + 1, before["order_count"] + 1, after["order_count"])
        self._assert(assertions, "inventory_decreased", after["product"].get("stock") == before["product"].get("stock") - quantity, before["product"].get("stock") - quantity, after["product"].get("stock"))
        expected_balance = round(before["user"].get("balance") - amount, 2)
        self._assert(assertions, "balance_decreased", after["user"].get("balance") == expected_balance, expected_balance, after["user"].get("balance"))

    @staticmethod
    def _assert(assertions, name, condition, expected, actual, detail=""):
        assertions.append(AssertionResult(name=name, status="passed" if condition else "failed", expected=expected, actual=actual, detail=detail))


def _request_view(method: str, path: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    return {"method": method, "path": path, "json": _redact(kwargs.get("json_body")), "params": _redact(kwargs.get("params"))}


def _response_view(response: requests.Response) -> dict[str, Any]:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text[:2000]
    return {"status_code": response.status_code, "json": _redact(body), "content_type": response.headers.get("content-type")}


def _redact(value: Any) -> Any:
    sensitive = {"password", "access_token", "authorization", "token", "secret", "database_url"}
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in sensitive else _redact(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact(child) for child in value]
    return value


def _response_view_of_none() -> dict[str, Any]:
    return {"status_code": None, "json": None}


def _schema_value(schema: Any) -> Any:
    """Deterministically fabricate one schema-valid value."""
    if not isinstance(schema, dict):
        return "test"
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if enum:
        return enum[0]
    value_type = schema.get("type")
    if value_type == "integer":
        if "minimum" in schema:
            return int(schema["minimum"])
        if "exclusiveMinimum" in schema:
            return int(schema["exclusiveMinimum"]) + 1
        return 1
    if value_type == "number":
        if "minimum" in schema:
            return schema["minimum"]
        if "exclusiveMinimum" in schema:
            return schema["exclusiveMinimum"] + 0.5
        return 1.0
    if value_type == "boolean":
        return True
    if value_type == "array":
        return [_schema_value(schema.get("items", {}))]
    if value_type == "object":
        return _body_from_schema(schema) or {}
    # string：优先满足 minLength，pattern 约束用大写试探
    min_length = int(schema.get("minLength", 0) or 0)
    max_length = schema.get("maxLength")
    if "pattern" in schema:
        value = "TEST" if min_length <= 4 else "T" * min_length
    else:
        value = "test" if min_length <= 4 else "x" * min_length
    if max_length and len(value) > int(max_length):
        value = value[: int(max_length)]
    return value


def _body_from_schema(schema: Any) -> dict[str, Any] | None:
    """Build a minimal request body from required properties only."""
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties", {})
    if not properties:
        return None
    required = schema.get("required") or list(properties.keys())
    body = {name: _schema_value(properties[name]) for name in required if name in properties}
    return body or None
