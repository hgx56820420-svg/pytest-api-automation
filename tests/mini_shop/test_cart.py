"""购物车：按用户隔离、添加商品。

用例编号来自 cases/mini_shop.yaml。
"""

import pytest


@pytest.mark.case("TC-CART-002")
def test_cart_is_isolated_per_user(api, create_product):
    """变异测试发现：去掉"只查当前用户"的条件后，原有测试全部仍然通过。"""
    p1, p2 = create_product(name="p1"), create_product(name="p2")
    user_a, user_b = api.register(), api.register()
    assert api.post("/api/cart/items", user=user_a, json={"product_id": p1["id"], "quantity": 1}).status_code == 201
    assert api.post("/api/cart/items", user=user_b, json={"product_id": p2["id"], "quantity": 1}).status_code == 201

    cart_a = api.get("/api/cart", user=user_a)
    cart_b = api.get("/api/cart", user=user_b)

    assert (cart_a.status_code, cart_b.status_code) == (200, 200)
    assert [item["product_id"] for item in cart_a.json()["items"]] == [p1["id"]]
    assert [item["product_id"] for item in cart_b.json()["items"]] == [p2["id"]]


@pytest.mark.case("TC-CART-004")
def test_add_item_to_cart(api, db, buyer, create_product):
    product = create_product(price=12.5, stock=10)

    response = api.post("/api/cart/items", user=buyer, json={"product_id": product["id"], "quantity": 2})

    assert response.status_code == 201
    [item] = response.json()["items"]
    assert (item["product_id"], item["quantity"]) == (product["id"], 2)
    assert (item["product"]["name"], item["product"]["price"]) == (product["name"], product["price"])
    rows = db.rows("cart_items", user_id=buyer.id, product_id=product["id"])
    assert [row["quantity"] for row in rows] == [2]
