"""支付与取消：REQ-ORDER-004 / REQ-ORDER-005 订单状态机。

用例编号来自 cases/mini_shop.yaml。
"""

import pytest


@pytest.fixture
def order(api, buyer, create_product):
    product = create_product(price=20, stock=5)
    response = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 2})
    assert response.status_code == 201
    return response.json()


def _money_and_stock(db, user, product_id):
    return db.row("users", user.id)["balance"], db.row("products", product_id)["stock"]


@pytest.mark.case("TC-ORDER-021")
def test_pay_deducts_balance_and_keeps_stock(api, db, buyer, order):
    balance_before, stock_before = _money_and_stock(db, buyer, order["product_id"])

    response = api.post(f"/api/orders/{order['id']}/pay", user=buyer)

    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert db.row("orders", order["id"])["status"] == "paid"
    assert _money_and_stock(db, buyer, order["product_id"]) == (balance_before - order["amount"], stock_before)


@pytest.mark.case("TC-ORDER-033")
def test_admin_cannot_pay_or_cancel_other_users_order(api, db, admin, buyer, order):
    before = (db.row("orders", order["id"])["status"], db.row("users", buyer.id)["balance"])

    responses = [
        api.get(f"/api/orders/{order['id']}", user=admin),
        api.post(f"/api/orders/{order['id']}/pay", user=admin),
        api.post(f"/api/orders/{order['id']}/cancel", user=admin),
    ]

    assert [r.status_code for r in responses] == [404, 404, 404]
    assert (db.row("orders", order["id"])["status"], db.row("users", buyer.id)["balance"]) == before


@pytest.mark.case("TC-ORDER-022")
def test_pay_twice_returns_409_and_changes_nothing(api, db, buyer, order):
    assert api.post(f"/api/orders/{order['id']}/pay", user=buyer).status_code == 200
    before = (
        _money_and_stock(db, buyer, order["product_id"]),
        db.count("inventory_transactions", product_id=order["product_id"]),
    )

    response = api.post(f"/api/orders/{order['id']}/pay", user=buyer)

    assert response.status_code == 409
    assert db.row("orders", order["id"])["status"] == "paid"
    after = (
        _money_and_stock(db, buyer, order["product_id"]),
        db.count("inventory_transactions", product_id=order["product_id"]),
    )
    assert after == before


@pytest.mark.case("TC-ORDER-025")
def test_pay_other_users_order_returns_404(api, db, buyer, order):
    stranger = api.register()
    balances_before = (db.row("users", buyer.id)["balance"], db.row("users", stranger.id)["balance"])

    response = api.post(f"/api/orders/{order['id']}/pay", user=stranger)

    assert response.status_code == 404
    assert db.row("orders", order["id"])["status"] == "created"
    assert (db.row("users", buyer.id)["balance"], db.row("users", stranger.id)["balance"]) == balances_before


@pytest.mark.case("TC-ORDER-027")
@pytest.mark.case("TC-INV-002")
def test_cancel_restores_stock_and_balance(api, db, buyer, create_product):
    product = create_product(price=20, stock=5)
    balance_before_order = db.row("users", buyer.id)["balance"]
    order = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 3}).json()

    response = api.post(f"/api/orders/{order['id']}/cancel", user=buyer)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert _money_and_stock(db, buyer, product["id"]) == (balance_before_order, 5)
    transactions = api.get(f"/api/inventory/{product['id']}/transactions", user=buyer).json()
    restore = [t for t in transactions if t["reason"] == "order_cancelled"]
    assert [(t["quantity_change"], t["reference_id"]) for t in restore] == [(3, order["id"])]
    assert sum(t["quantity_change"] for t in transactions) == 0


@pytest.mark.case("TC-ORDER-029")
def test_cancel_paid_or_cancelled_order_returns_409(api, db, buyer, create_product):
    product = create_product(price=20, stock=10)
    paid = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 1}).json()
    assert api.post(f"/api/orders/{paid['id']}/pay", user=buyer).status_code == 200
    cancelled = api.post("/api/orders", user=buyer, json={"product_id": product["id"], "quantity": 1}).json()
    assert api.post(f"/api/orders/{cancelled['id']}/cancel", user=buyer).status_code == 200
    before = _money_and_stock(db, buyer, product["id"])

    responses = [api.post(f"/api/orders/{o['id']}/cancel", user=buyer) for o in (paid, cancelled)]

    assert [r.status_code for r in responses] == [409, 409]
    assert db.row("orders", paid["id"])["status"] == "paid"
    assert db.row("orders", cancelled["id"])["status"] == "cancelled"
    assert _money_and_stock(db, buyer, product["id"]) == before
