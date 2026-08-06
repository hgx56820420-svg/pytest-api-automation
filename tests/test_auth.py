import uuid

import requests


def test_register_success():
    username = f"user_{uuid.uuid4().hex[:8]}"
    request_body = {
        "username": username,
        "password": "Test123456",
    }

    response = requests.post(
        "http://127.0.0.1:8010/api/auth/register",
        json=request_body,
        timeout=5,
    )

    assert response.status_code == 201, response.text

def test_register_duplicate_username():
    username = f"user_{uuid.uuid4().hex[:8]}"
    request_body = {
        "username": username,
        "password": "Test123456",
    }

    first_response = requests.post(
        "http://127.0.0.1:8010/api/auth/register",
        json=request_body,
        timeout=5,
    )

    assert first_response.status_code == 201, first_response.text

    second_response = requests.post(
        "http://127.0.0.1:8010/api/auth/register",
        json=request_body,
        timeout=5,
    )
    assert second_response.status_code == 409, second_response.text

def test_login_success():
    username = f"user_{uuid.uuid4().hex[:8]}"
    request_body = {
        "username": username,
        "password": "Test123456",
    }

    first_response = requests.post(
        "http://127.0.0.1:8010/api/auth/register",
        json=request_body,
        timeout=5,
    )
    assert first_response.status_code == 201, first_response.text

    second_response = requests.post(
        "http://127.0.0.1:8010/api/auth/login",
        json=request_body,
        timeout=5,
    )
    assert second_response.status_code == 200, second_response.text
    assert "access_token" in second_response.json(), second_response.text

def test_login_wrong_password():
    username = f"user_{uuid.uuid4().hex[:8]}"
    request_body = {
        "username": username,
        "password": "Test1234567",
    }

    first_response = requests.post(
        "http://127.0.0.1:8010/api/auth/register",
        json=request_body,
        timeout=5,
    )

    assert first_response.status_code == 201, first_response.text

    wrong_login_body = {
        "username": username,
        "password": "Wrong1234567",
    }
    second_response = requests.post(
        "http://127.0.0.1:8010/api/auth/login",
        json=wrong_login_body,
        timeout=5,  
    )
    assert second_response.status_code == 401, second_response.text

def test_me_without_token():
   
    response = requests.get(
        "http://127.0.0.1:8010/api/auth/me",
        timeout=5,
    )

    assert response.status_code == 401, response.text

