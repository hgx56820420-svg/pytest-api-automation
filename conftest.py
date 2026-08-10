import pytest
import uuid
import requests
import os
from jsonschema import validate
from faker import Faker
fake=Faker()

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8010")

@pytest.fixture
def base_url(): 
    return BASE_URL 

@pytest.fixture
def registered_user(base_url):
    username = f"user_{uuid.uuid4().hex[:8]}"
    password = "Test123456"

    response = requests.post(
        base_url + "/api/auth/register",
        json={"username": username, "password": password},
        timeout=5,
    )
    assert response.status_code == 201, response.text

    return {"username": username, "password": password}

TOKEN_SCHEMA = {
    "type": "object",
    "properties": {
        "access_token": {"type": "string"},
        "token_type": {"type": "string"},
    },
    "required": ["access_token", "token_type"],
}

@pytest.fixture
def auth_token(base_url, registered_user):
    response = requests.post(
        base_url + "/api/auth/login",
        json=registered_user,
        timeout=5,
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]

@pytest.fixture
def auth_headers(base_url, auth_token):
    return {"Authorization": f"Bearer {auth_token}"}

@pytest.fixture
def created_product(base_url,auth_headers):
    product_body={
        "name": fake.word(),
        "price": 10.0,
        "stock": 5,
    }
    response = requests.post(
        base_url + "/api/products",
        json=product_body,
        headers=auth_headers,
        timeout=5,
    )
    assert response.status_code == 201, response.text
    return response.json()
