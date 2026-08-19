import requests
import uuid


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

def test_create_order_success(base_url,auth_headers,created_product):
    # 下单（quantity=2，总价 = 10.0 * 2 = 20.0）
    order_response = requests.post(
        base_url + "/api/orders",
        headers=auth_headers,
        json={"product_id": created_product["id"], "quantity": 2},
        timeout=5,
    )
    assert order_response.status_code == 201, order_response.text
    order_data = order_response.json()
    assert order_data["status"] == "created"
    assert order_data["amount"] == 20.0
    assert order_data["product_id"] == created_product["id"]

def test_create_order_insufficient_stock(base_url, auth_headers, created_product):
    # created_product 固定 stock=5，这里 quantity 故意超过库存
    payload = {
        "product_id": created_product["id"],   # 从 created_product 里拿什么字段？
        "quantity": 6       # 故意设置成超过 5 的值
    }
    resp = requests.post(base_url + "/api/orders", json=payload, headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json().get('detail') == 'Insufficient stock'

def test_create_order_insufficient_balance(base_url, auth_headers):
    # 1. 直接在测试内创建一个高价商品（不用 created_product fixture）
    product_resp = requests.post(
        base_url + "/api/products",
        json={"name": "test_expensive", "price": 2000, "stock": 1},
        headers=auth_headers,
        timeout=5,
    )
    assert product_resp.status_code == 201, product_resp.text
    product_id = product_resp.json()["id"]

    # 2. 下单，quantity * price 超过余额 1000
    order_resp = requests.post(
        base_url + "/api/orders",
        json={"product_id": product_id, "quantity": 1},
        headers=auth_headers,
        timeout=5,
    )
    assert order_resp.status_code == 400
    assert order_resp.json().get("detail") == "Insufficient balance"

def test_create_order_product_not_found(base_url,auth_headers):
    order_response = requests.post(
            base_url + "/api/orders",
            headers=auth_headers,
            json={"product_id": 999999,"quantity":1},
            timeout=5,
        )
    assert order_response.status_code==404
    assert order_response.json().get("detail") == "Product not found"

def test_create_order_invalid_quantity(base_url, auth_headers, created_product):
    order_response = requests.post(
        base_url+"/api/orders",
        headers=auth_headers,
        json={
            "product_id": created_product["id"],
            "quantity": 0,
        },
        timeout=5,
    )

    assert order_response.status_code==422

def test_pay_order_twice_should_fail(base_url,auth_headers,created_product):
    order_response = requests.post(
        base_url+"/api/orders",
        headers=auth_headers,
        json={
            "product_id": created_product["id"],
            "quantity": 1,
        },
        timeout=5,
    )
    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["id"]
    pay_response=requests.post(
        base_url+f"/api/orders/{order_id}/pay",
        headers=auth_headers,
        timeout=5,
    )
    assert pay_response.status_code==200,pay_response.text

    re_pay_response=requests.post(
        base_url+f"/api/orders/{order_id}/pay",
        headers=auth_headers,
        timeout=5,

    )
    assert re_pay_response.status_code==409,re_pay_response.text
    assert re_pay_response.json().get("detail") == "Cannot pay an order in status 'paid'"

def test_cancel_paid_order_should_fail(base_url,auth_headers,created_product):
    order_response=requests.post(
        base_url+"/api/orders",
        headers=auth_headers,
        json={
            "product_id": created_product["id"],
            "quantity": 1,
        }

    )
    assert order_response.status_code==201
    order_id = order_response.json()["id"]
    pay_response=requests.post(
        base_url+f"/api/orders/{order_id}/pay",
        headers=auth_headers,
        timeout=5,
    )
    assert pay_response.status_code==200,pay_response.text

    cancel_response=requests.post(
        base_url+f"/api/orders/{order_id}/cancel",
        headers=auth_headers,
        timeout=5,
    )
    assert cancel_response.status_code==409,cancel_response.text
    assert cancel_response.json().get("detail")=="Cannot cancel an order in status 'paid'"

def test_cancel_cancelled_order_should_fail(base_url,auth_headers,created_product):
    order_response=requests.post(
        base_url+"/api/orders",
        headers=auth_headers,
        json={
            "product_id": created_product["id"],
            "quantity": 1,
        }
    )
    order_id=order_response.json()["id"]
    cancel_response=requests.post(
        base_url+f"/api/orders/{order_id}/cancel",
        headers=auth_headers,
        timeout=5,
    )
    assert cancel_response.status_code==200,cancel_response.text

    re_cancel_response=requests.post(
        base_url+f"/api/orders/{order_id}/cancel",
        headers=auth_headers,
        timeout=5,
    )
    assert re_cancel_response.status_code==409,cancel_response.text
    assert re_cancel_response.json().get("detail")==("Cannot cancel an order in status 'cancelled'")

def test_user_cannot_access_another_users_order(
    base_url,
    auth_headers,
    created_product,
):
    # 用户 A：使用现有 auth_headers 创建订单
    order_response = requests.post(
        base_url + "/api/orders",
        headers=auth_headers,
        json={
            "product_id": created_product["id"],
            "quantity": 1,
        },
        timeout=5,
    )

    assert order_response.status_code == 201, order_response.text
    order_id = order_response.json()["id"]

    # 用户 B：注册一个新账号
    user_b = {
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "password": "Test123456",
    }

    register_response = requests.post(
        base_url + "/api/auth/register",
        json=user_b,
        timeout=5,
    )

    assert register_response.status_code == 201, register_response.text

    # 用户 B：登录获取自己的 token
    login_response = requests.post(
        base_url + "/api/auth/login",
        json=user_b,
        timeout=5,
    )

    assert login_response.status_code == 200, login_response.text
    token_b = login_response.json()["access_token"]
    headers_b = {
        "Authorization": f"Bearer {token_b}"
    }

    # 用户 B：查询用户 A 的订单
    get_order_response = requests.get(
        base_url + f"/api/orders/{order_id}",
        headers=headers_b,
        timeout=5,
    )

    # 预期服务端隐藏订单是否存在
    assert get_order_response.status_code == 404
    assert get_order_response.json().get("detail") == "Order not found"
