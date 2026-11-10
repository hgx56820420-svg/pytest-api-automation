"""购买：REQ-ORDER-001 下单前置条件、副作用和失败不变量，REQ-COUPON 优惠券扣款。

用例编号来自 cases/mini_shop.yaml。
"""

import uuid

import pytest


def _snapshot(db, user, product_id):
    return {
        "balance": db.row("users", user.id)["balance"],
        "stock": db.row("products", product_id)["stock"],
        "orders": db.count("orders", user_id=user.id),
    }


@pytest.mark.case("TC-ORDER-001")
@pytest.mark.case("TC-INV-001")
def test_buy_deducts_stock_and_balance(api, db, buyer, create_product):
    product = create_product(price=99.0, stock=10)
    before = _snapshot(db, buyer, product["id"])
    transactions_before = len(api.get(f"/api/inventory/{product['id']}/transactions", user=buyer).json())

    response = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 2})

    assert response.status_code == 201
    order = response.json()
    assert (order["amount"], order["status"], order["user_id"], order["quantity"]) == (198.0, "created", buyer.id, 2)
    after = _snapshot(db, buyer, product["id"])
    # 下单只扣库存，余额在支付时扣
    assert after == {"balance": before["balance"], "stock": 8, "orders": before["orders"] + 1}
    transactions = api.get(f"/api/inventory/{product['id']}/transactions", user=buyer)
    assert transactions.status_code == 200
    assert len(transactions.json()) == transactions_before + 1
    latest = transactions.json()[-1]
    assert (latest["product_id"], latest["quantity_change"], latest["reason"]) == (product["id"], -2, "order_created")
    assert latest["reference_id"] == order["id"]


@pytest.mark.case("TC-ORDER-005")
def test_buy_unknown_product_returns_404(api, db, buyer):
    balance_before = db.row("users", buyer.id)["balance"]
    orders_before = db.count("orders")

    response = api.post("/api/orders", user=buyer, json={"product_id": 999_999, "quantity": 1})

    assert response.status_code == 404
    assert response.json()["detail"] == "Product not found"
    assert db.row("users", buyer.id)["balance"] == balance_before
    assert db.count("orders") == orders_before


@pytest.mark.case("TC-ORDER-006")
def test_buy_off_sale_product_changes_nothing(api, db, admin, buyer, create_product):
    product = create_product(stock=5)
    assert api.delete(f"/api/products/{product['id']}", user=admin).status_code == 200
    before = _snapshot(db, buyer, product["id"])

    response = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 1})

    assert response.status_code == 400
    assert response.json()["detail"] == "Product is not on sale"
    assert _snapshot(db, buyer, product["id"]) == before


@pytest.mark.case("TC-ORDER-007")
def test_stock_boundary(api, db, create_product):
    product = create_product(price=1, stock=3)
    buyer_a = api.register()
    before = _snapshot(db, buyer_a, product["id"])

    too_many = api.post("/api/orders", user=buyer_a, json={"product_id": product["id"], "quantity": 4})
    assert too_many.status_code == 400
    assert too_many.json()["detail"] == "Insufficient stock"
    assert _snapshot(db, buyer_a, product["id"]) == before

    exact = api.post("/api/orders", user=buyer_a, json={"product_id": product["id"], "quantity": 3})
    assert exact.status_code == 201
    assert db.row("products", product["id"])["stock"] == 0

    buyer_b = api.register()
    before_b = _snapshot(db, buyer_b, product["id"])
    sold_out = api.post("/api/orders", user=buyer_b, json={"product_id": product["id"], "quantity": 1})
    assert sold_out.status_code == 400
    assert sold_out.json()["detail"] == "Insufficient stock"
    assert _snapshot(db, buyer_b, product["id"]) == before_b


@pytest.mark.case("TC-ORDER-009")
def test_balance_boundary_at_payment(api, db, create_product):
    # 新用户默认余额 1000；余额在支付时校验和扣减
    exact_product = create_product(price=500, stock=10)
    buyer = api.register()
    order = api.post("/api/orders", user=buyer, json={"product_id": exact_product["id"], "quantity": 2}).json()

    assert api.post(f"/api/orders/{order['id']}/pay", user=buyer).status_code == 200
    assert db.row("users", buyer.id)["balance"] == 0

    pricey = create_product(price=1000.01, stock=10)
    other = api.register()
    pricey_order = api.post("/api/orders", user=other, json={"product_id": pricey["id"], "quantity": 1})
    assert pricey_order.status_code == 201
    before = _snapshot(db, other, pricey["id"])

    too_expensive = api.post(f"/api/orders/{pricey_order.json()['id']}/pay", user=other)

    assert too_expensive.status_code == 400
    assert too_expensive.json()["detail"] == "Insufficient balance"
    assert _snapshot(db, other, pricey["id"]) == before
    assert db.row("orders", pricey_order.json()["id"])["status"] == "created"


@pytest.mark.case("TC-ORDER-012")
def test_order_endpoints_without_token_return_401(api, db, buyer, create_product):
    product = create_product(stock=5)
    order = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 1}).json()
    before = (_snapshot(db, buyer, product["id"]), db.row("orders", order["id"])["status"])

    calls = [
        api.post("/api/orders", json={"product_id": product["id"], "quantity": 1}),
        api.get("/api/orders"),
        api.get(f"/api/orders/{order['id']}"),
        api.post(f"/api/orders/{order['id']}/pay"),
        api.post(f"/api/orders/{order['id']}/cancel"),
    ]

    assert [r.status_code for r in calls] == [401] * 5
    assert {r.json()["detail"] for r in calls} == {"Invalid or missing credentials"}
    assert (_snapshot(db, buyer, product["id"]), db.row("orders", order["id"])["status"]) == before


@pytest.mark.case("TC-COUPON-007")
def test_buy_with_coupon_charges_discounted_amount(api, db, admin, buyer, create_product):
    product = create_product(price=100, stock=10)
    code = f"C{uuid.uuid4().hex[:8].upper()}"
    coupon = api.post("/api/coupons", user=admin, json={"code": code, "discount_percent": 10, "max_uses": 5})
    assert coupon.status_code == 201
    before = _snapshot(db, buyer, product["id"])

    response = api.post(
        "/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 2, "coupon_code": code}
    )

    assert response.status_code == 201
    assert (response.json()["amount"], response.json()["coupon_code"]) == (180.0, code)
    after = _snapshot(db, buyer, product["id"])
    assert after == {"balance": before["balance"], "stock": 8, "orders": before["orders"] + 1}
    assert db.rows("coupons", code=code)[0]["used_count"] == 1

    assert api.post(f"/api/orders/{response.json()['id']}/pay", user=buyer).status_code == 200
    assert db.row("users", buyer.id)["balance"] == before["balance"] - 180.0
