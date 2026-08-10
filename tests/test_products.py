import requests
from faker import Faker
import pytest

fake = Faker()


def test_create_product_success(base_url, auth_token):
    product_body = {
        "name": fake.word(),
        "price": fake.pyfloat(min_value=1, max_value=1000, right_digits=2),
        "stock": fake.random_int(min=1, max=100),
    }

    response = requests.post(
        base_url + "/api/products",
        json=product_body,
        headers={"Authorization": f"Bearer {auth_token}"},
        timeout=5,
    )
    assert response.status_code == 201, response.text

def test_create_product_without_token(base_url):
    product_body = {
        "name": fake.word(),
        "price": fake.pyfloat(min_value=1, max_value=1000, right_digits=2),
        "stock": fake.random_int(min=1, max=100),
    }

    response = requests.post(
        base_url + "/api/products", 
        json=product_body, 
        timeout=5,)
    assert response.status_code == 401, response.text

def test_get_product_not_found(base_url):
    response = requests.get(
        base_url + "/api/products/999999",
        timeout=5,)
    assert response.status_code == 404, response.text

def test_create_product_invalid_price(base_url, auth_token):
    product_body = {
        "name": fake.word(),
        "price": 0,
        "stock": fake.random_int(min=1, max=100),
    }

    response = requests.post(
        base_url + "/api/products",
        json=product_body,
        headers={"Authorization": f"Bearer {auth_token}"},
        timeout=5,
    )
    assert response.status_code == 422, response.text

@pytest.mark.parametrize("query_params", [
    {"page": -1},
    {"size": 1000},
])
def test_list_products_invalid_pagination(base_url, query_params):
    response = requests.get(
        base_url + "/api/products",
        params=query_params,
        timeout=5,
    )
    assert response.status_code == 422, response.text