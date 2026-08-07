import uuid
import pytest
import requests
from conftest import TOKEN_SCHEMA
from jsonschema import validate

@pytest.mark.parametrize("username_length,expected_status", [
    (2, 422),
    (3, 201),
    (20, 201),
    (21, 422),
])
def test_register_success(base_url,username_length,expected_status):
    username =  f"u{uuid.uuid4().hex}"[:username_length]
    request_body = {
        "username": username,
        "password": "Test123456",
    }

    response = requests.post(
        base_url+"/api/auth/register",
        json=request_body,
        timeout=5,
    )

    assert response.status_code == expected_status, response.text

def test_register_duplicate_username(base_url):
    username = f"user_{uuid.uuid4().hex[:8]}"
    request_body = {
        "username": username,
        "password": "Test123456",
    }

    first_response = requests.post(
        base_url+"/api/auth/register",
        json=request_body,
        timeout=5,
    )

    assert first_response.status_code == 201, first_response.text

    second_response = requests.post(
        base_url+"/api/auth/register",
        json=request_body,
        timeout=5,
    )
    assert second_response.status_code == 409, second_response.text

def test_login_success(base_url,registered_user):
    
    response = requests.post(
        base_url+"/api/auth/login",
        json=registered_user,
        timeout=5,
    )
 
    assert response.status_code == 200, response.text
    validate(instance=response.json(), schema=TOKEN_SCHEMA)
 

def test_login_wrong_password(base_url,registered_user):
    wrong_login_body={
        "username": registered_user["username"],
        "password": "wrong_password999"
    }


    response = requests.post(
        base_url+"/api/auth/login",
        json=wrong_login_body,
        timeout=5,  
    )
    assert response.status_code == 401, response.text

def test_me_without_token(base_url):
   
    response = requests.get(
        base_url+"/api/auth/me",
        timeout=5,
    )

    assert response.status_code == 401, response.text

