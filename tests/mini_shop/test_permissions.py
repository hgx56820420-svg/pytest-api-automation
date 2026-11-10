"""权限：商品和优惠券只能由管理员管理（需求变更：新增管理员角色）。

用例编号来自 cases/mini_shop.yaml。
"""

import uuid

import pytest


@pytest.mark.case("TC-PERM-001")
@pytest.mark.case("TC-PERM-002")
def test_registered_user_cannot_create_product(api, db, buyer):
    products_before = db.count("products")

    response = api.post("/api/products", user=buyer, json={"name": "x", "price": 1, "stock": 1})

    assert response.status_code == 403
    assert db.count("products") == products_before


@pytest.mark.case("TC-PERM-003")
def test_normal_user_cannot_update_product(api, db, buyer, create_product):
    product = create_product(price=50, stock=3)
    before = db.row("products", product["id"])

    response = api.put(f"/api/products/{product['id']}", user=buyer, json={"price": 1, "stock": 999})

    assert response.status_code == 403
    assert db.row("products", product["id"]) == before


@pytest.mark.case("TC-PERM-004")
def test_normal_user_cannot_take_product_off_sale(api, db, buyer, create_product):
    product = create_product()

    response = api.delete(f"/api/products/{product['id']}", user=buyer)

    assert response.status_code == 403
    assert db.row("products", product["id"])["status"] == "on_sale"


@pytest.mark.case("TC-PERM-005")
def test_normal_user_cannot_create_coupon(api, db, buyer):
    """原实现里普通用户能建 100% 折扣券，再用它 0 元下单。"""
    code = f"C{uuid.uuid4().hex[:8].upper()}"
    coupons_before = db.count("coupons")

    response = api.post("/api/coupons", user=buyer, json={"code": code, "discount_percent": 10, "max_uses": 1})

    assert response.status_code == 403
    assert db.count("coupons") == coupons_before
    assert db.count("coupons", code=code) == 0


@pytest.mark.case("TC-PERM-006")
def test_admin_endpoints_without_token_return_401(api, db, create_product):
    product = create_product()
    before = (db.row("products", product["id"]), db.count("products"), db.count("coupons"))

    calls = [
        api.post("/api/products", json={"name": "x", "price": 1, "stock": 1}),
        api.put(f"/api/products/{product['id']}", json={"price": 1}),
        api.delete(f"/api/products/{product['id']}"),
        api.post("/api/coupons", json={"code": "NOAUTH1", "discount_percent": 10, "max_uses": 1}),
    ]

    assert [r.status_code for r in calls] == [401] * 4
    assert (db.row("products", product["id"]), db.count("products"), db.count("coupons")) == before
