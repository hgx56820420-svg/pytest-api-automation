import pytest


@pytest.fixture(scope="session")
def api_target(demo_service):
    return demo_service("mini_shop")


@pytest.fixture
def create_product(api, admin):
    """管理员创建商品，返回响应 JSON。"""

    def create(price=10.0, stock=10, name="demo product"):
        response = api.post("/api/products", user=admin, json={"name": name, "price": price, "stock": stock})
        assert response.status_code == 201, response.text
        return response.json()

    return create


@pytest.fixture
def buyer(api):
    return api.register()
