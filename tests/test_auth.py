import uuid

import requests




def test_register_success(base_url):
    username = f"user_{uuid.uuid4().hex[:8]}"
    request_body = {
        "username": username,
        "password": "Test123456",
    }

    response = requests.post(
        base_url+"/api/auth/register",
        json=request_body,
        timeout=5,
    )

    assert response.status_code == 201, response.text

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
    assert "access_token" in response.json(), response.text

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

