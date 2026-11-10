"""并发：同一资源被同时操作时，唯一性和数量不能出错。

用例编号来自 cases/mini_shop.yaml。
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest


def _concurrently(count, call):
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(lambda _: call(), range(count)))


@pytest.mark.case("TC-AUTH-005")
def test_concurrent_register_same_username(api, db):
    username = f"c{uuid.uuid4().hex[:12]}"

    statuses = _concurrently(
        10, lambda: api.post("/api/auth/register", json={"username": username, "password": "Test123456"}).status_code
    )

    assert sorted(statuses) == [201] + [409] * 9
    assert db.count("users", username=username) == 1


@pytest.mark.case("TC-COUPON-005")
def test_concurrent_create_same_coupon_code(api, db, admin):
    code = f"C{uuid.uuid4().hex[:8].upper()}"
    body = {"code": code, "discount_percent": 10, "max_uses": 1}

    statuses = _concurrently(5, lambda: api.post("/api/coupons", user=admin, json=body).status_code)

    assert sorted(statuses) == [201] + [409] * 4
    assert db.count("coupons", code=code) == 1


@pytest.mark.case("TC-CART-007")
def test_concurrent_add_same_product_to_cart(api, db, buyer, create_product):
    product = create_product(stock=10)

    statuses = _concurrently(
        5, lambda: api.post("/api/cart/items", user=buyer, json={"product_id": product["id"], "quantity": 1}).status_code
    )

    rows = db.rows("cart_items", user_id=buyer.id, product_id=product["id"])
    assert statuses.count(201) == 5
    assert len(rows) == 1
    assert rows[0]["quantity"] == 5


@pytest.mark.case("TC-ORDER-008")
def test_concurrent_orders_do_not_oversell(api, db, create_product):
    product = create_product(price=1, stock=5)
    buyers = [api.register() for _ in range(10)]
    body = {"product_id": product["id"], "quantity": 1}

    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda b: api.post("/api/orders", user=b, json=body), buyers))

    succeeded = [r for r in responses if r.status_code == 201]
    assert len(succeeded) == 5
    assert all(r.status_code == 400 and r.json()["detail"] == "Insufficient stock" for r in responses if r not in succeeded)
    assert db.row("products", product["id"])["stock"] == 0
    assert db.count("orders", product_id=product["id"]) == 5


@pytest.mark.case("TC-ORDER-030")
def test_concurrent_cancel_restores_once(api, db, buyer, create_product):
    product = create_product(price=10, stock=5)
    balance_before = db.row("users", buyer.id)["balance"]
    order = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 2}).json()

    statuses = _concurrently(10, lambda: api.post(f"/api/orders/{order['id']}/cancel", user=buyer).status_code)

    assert sorted(statuses) == [200] + [409] * 9
    assert db.row("products", product["id"])["stock"] == 5
    assert db.row("users", buyer.id)["balance"] == balance_before
    assert db.count("inventory_transactions", reference_id=order["id"], reason="order_cancelled") == 1


@pytest.mark.case("TC-COUPON-010")
def test_concurrent_orders_cannot_overuse_coupon(api, db, admin, create_product):
    product = create_product(price=10, stock=50)
    code = f"C{uuid.uuid4().hex[:8].upper()}"
    assert api.post("/api/coupons", user=admin, json={"code": code, "discount_percent": 10, "max_uses": 1}).status_code == 201
    buyers = [api.register() for _ in range(5)]
    body = {"product_id": product["id"], "quantity": 1, "coupon_code": code}

    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(lambda b: api.post("/api/orders", user=b, json=body).status_code, buyers))

    assert sorted(statuses) == [201] + [400] * 4
    assert db.rows("coupons", code=code)[0]["used_count"] == 1
    assert db.count("orders", coupon_code=code) == 1
    assert db.row("products", product["id"])["stock"] == 49


@pytest.mark.case("TC-ORDER-023")
def test_concurrent_pay_charges_once(api, db, buyer, create_product):
    product = create_product(price=30, stock=5)
    order = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 2}).json()
    balance_before = db.row("users", buyer.id)["balance"]

    statuses = _concurrently(10, lambda: api.post(f"/api/orders/{order['id']}/pay", user=buyer).status_code)

    assert sorted(statuses) == [200] + [409] * 9
    assert db.row("orders", order["id"])["status"] == "paid"
    assert db.row("users", buyer.id)["balance"] == balance_before - 60


@pytest.mark.case("TC-ORDER-010")
def test_concurrent_payments_never_overdraw_balance(api, db, buyer, create_product):
    # 余额 1000，5 笔 300 元的订单同时支付：最多成功 3 笔
    product = create_product(price=300, stock=10)
    orders = [
        api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 1}).json() for _ in range(5)
    ]

    with ThreadPoolExecutor(max_workers=5) as pool:
        statuses = list(pool.map(lambda o: api.post(f"/api/orders/{o['id']}/pay", user=buyer).status_code, orders))

    assert sorted(statuses) == [200] * 3 + [400] * 2
    assert db.row("users", buyer.id)["balance"] == 100
    assert db.count("orders", user_id=buyer.id, status="paid") == 3


@pytest.mark.case("TC-ORDER-028")
def test_pay_and_cancel_race_has_one_winner(api, db, buyer, create_product):
    product = create_product(price=50, stock=5)
    balance_before = db.row("users", buyer.id)["balance"]
    order = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 1}).json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        pay = pool.submit(api.post, f"/api/orders/{order['id']}/pay", user=buyer)
        cancel = pool.submit(api.post, f"/api/orders/{order['id']}/cancel", user=buyer)
        statuses = sorted([pay.result().status_code, cancel.result().status_code])

    assert statuses == [200, 409]
    final = db.row("orders", order["id"])["status"]
    stock, balance = db.row("products", product["id"])["stock"], db.row("users", buyer.id)["balance"]
    if final == "paid":
        assert (stock, balance) == (4, balance_before - 50)
    else:
        assert final == "cancelled"
        assert (stock, balance) == (5, balance_before)
