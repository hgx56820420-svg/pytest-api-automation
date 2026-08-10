import requests


def test_cancel_order_restores_stock_and_balance(base_url, auth_headers, created_product):
    # 1. 查询商品初始库存
    stock_resp = requests.get(
        base_url + f"/api/products/{created_product['id']}",
        timeout=5,
    )
    assert stock_resp.status_code == 200, stock_resp.text
    initial_stock = stock_resp.json()["stock"]

    # 2. 查询用户初始余额
    me_resp = requests.get(
        base_url + "/api/auth/me",
        headers=auth_headers,
        timeout=5,
    )
    assert me_resp.status_code == 200, me_resp.text
    initial_balance = me_resp.json()["balance"]

    # 3. 下单（quantity=2，总价 = 10.0 * 2 = 20.0）
    order_response = requests.post(
        base_url + "/api/orders",
        headers=auth_headers,
        json={"product_id": created_product["id"], "quantity": 2},
        timeout=5,
    )
    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["id"]

    # 4. 取消订单
    cancel_response = requests.post(
        base_url + f"/api/orders/{order_id}/cancel",
        headers=auth_headers,
        timeout=5,
    )
    assert cancel_response.status_code == 200, cancel_response.text

    # 5. 重新查询商品库存和用户余额
    final_stock_resp = requests.get(
        base_url + f"/api/products/{created_product['id']}",
        timeout=5,
    )
    assert final_stock_resp.status_code == 200, final_stock_resp.text
    final_stock = final_stock_resp.json()["stock"]

    final_balance_resp = requests.get(
        base_url + "/api/auth/me",
        headers=auth_headers,
        timeout=5,
    )
    assert final_balance_resp.status_code == 200, final_balance_resp.text
    final_balance = final_balance_resp.json()["balance"]

    # 6. 断言：副作用真的回滚了（库存和余额都恢复到下单前的值）
    assert final_stock == initial_stock, f"stock not restored: {final_stock} != {initial_stock}"
    assert final_balance == initial_balance, f"balance not restored: {final_balance} != {initial_balance}"
