"""响应契约校验：状态码未声明、字段多出、类型不符、接口不存在都要报出来。"""

import json

import pytest
import requests
import urllib3

from api_agent.contract import ContractValidator, ContractViolation

SPEC = {
    "openapi": "3.1.0",
    "info": {"title": "t", "version": "1"},
    "paths": {
        "/api/orders/{order_id}": {
            "get": {
                "parameters": [{"name": "order_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                "responses": {
                    "200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}}},
                    "404": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
        }
    },
    "components": {
        "schemas": {
            "Order": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "amount": {"type": "number"}},
                "required": ["id", "amount"],
            },
            "Error": {"type": "object", "properties": {"detail": {"type": "string"}}, "required": ["detail"]},
        }
    },
}


@pytest.fixture
def validator(tmp_path):
    path = tmp_path / "shop.openapi.json"
    path.write_text(json.dumps(SPEC), encoding="utf-8")
    return ContractValidator(path)


def _response(status: int, body) -> requests.Response:
    content = json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    response = requests.Response()
    response.status_code = status
    response._content = content
    response.headers.update(headers)
    # Schemathesis 从底层 urllib3 响应读取响应头
    response.raw = urllib3.HTTPResponse(body=content, headers=headers, status=status)
    response.request = requests.Request("GET", "http://127.0.0.1/api/orders/1").prepare()
    return response


def test_matching_response_passes(validator):
    # check() 不抛异常就表示符合契约
    assert validator.check("GET", "/api/orders/1", _response(200, {"id": 1, "amount": 9.5})) is None
    assert validator.check("GET", "/api/orders/1", _response(404, {"detail": "Order not found"})) is None


def test_undocumented_status_code_fails(validator):
    with pytest.raises(ContractViolation, match="returned 409"):
        validator.check("GET", "/api/orders/1", _response(409, {"detail": "x"}))


def test_renamed_field_fails(validator):
    with pytest.raises(ContractViolation, match='"amount" is a required property'):
        validator.check("GET", "/api/orders/1", _response(200, {"id": 1, "price": 9.5}))


def test_undocumented_extra_field_fails(validator):
    with pytest.raises(ContractViolation, match=r"fields not in contract: \$\.coupon"):
        validator.check("GET", "/api/orders/1", _response(200, {"id": 1, "amount": 9.5, "coupon": "X"}))


def test_unknown_operation_fails(validator):
    with pytest.raises(ContractViolation, match="not in contract"):
        validator.check("DELETE", "/api/orders/1", _response(200, {}))
