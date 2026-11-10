"""HTTP 客户端：只允许发往 base_url 所在主机，每个请求带 X-Request-ID 并记录证据。"""

import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests

from api_agent.evidence import Recorder


class UnsafePathError(ValueError):
    """路径不是以单个 / 开头的相对路径，可能把请求（和 token）发到别的主机。"""


@dataclass
class User:
    id: int
    username: str
    password: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@dataclass
class ApiTarget:
    """被测服务的连接信息。认证接口的路径和字段按这个项目的约定给了默认值。"""

    base_url: str
    db_url: str | None = None
    admin_username: str | None = None
    admin_password: str | None = None
    contract_path: str | None = None
    register_path: str = "/api/auth/register"
    login_path: str = "/api/auth/login"
    me_path: str = "/api/auth/me"
    token_field: str = "access_token"


class ApiClient:
    def __init__(self, target: ApiTarget, recorder: Recorder, timeout: float = 10, contract=None):
        self.target = target
        self.base_url = target.base_url.rstrip("/")
        self._netloc = urlsplit(self.base_url).netloc
        self.recorder = recorder
        self.timeout = timeout
        self.session = requests.Session()
        self.contract = contract  # ContractValidator：配置了契约时，每个响应都按契约校验

    def close(self) -> None:
        self.session.close()

    def _url(self, path: str) -> str:
        if not path.startswith("/") or path.startswith("//") or "\\" in path:
            raise UnsafePathError(f"path must be a relative path starting with '/': {path!r}")
        url = self.base_url + path
        if urlsplit(url).netloc != self._netloc:
            raise UnsafePathError(f"path would leave {self._netloc}: {path!r}")
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        user: User | None = None,
        json: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        check_contract: bool = True,
    ) -> requests.Response:
        request_id = uuid.uuid4().hex[:12]
        all_headers = {**(headers or {}), **(user.headers if user else {}), "X-Request-ID": request_id}
        # 不自动跟随重定向：测试可以直接断言 302 和 Location，token 也不会跟着跳走
        response = self.session.request(
            method,
            self._url(path),
            json=json,
            params=params,
            headers=all_headers,
            timeout=self.timeout,
            allow_redirects=False,
        )
        self.recorder.http(request_id=request_id, method=method, path=path, params=params, body=json, response=response)
        if self.contract is not None and check_contract:
            self.contract.check(method, path, response)
        return response

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs) -> requests.Response:
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs) -> requests.Response:
        return self.request("DELETE", path, **kwargs)

    def register(self, username: str | None = None, password: str = "Test123456") -> User:
        """注册一个新用户并登录。默认用户名随机，测试之间互不影响，可以并行跑。"""
        username = username or f"u{uuid.uuid4().hex[:12]}"
        response = self.post(self.target.register_path, json={"username": username, "password": password})
        assert response.status_code == 201, f"register failed: {response.status_code} {response.text[:200]}"
        return self.login(username, password, user_id=response.json()["id"])

    def login(self, username: str, password: str, user_id: int | None = None) -> User:
        response = self.post(self.target.login_path, json={"username": username, "password": password})
        assert response.status_code == 200, f"login failed for {username}: {response.status_code}"
        token = response.json()[self.target.token_field]
        if user_id is None:
            user_id = self.get(self.target.me_path, headers={"Authorization": f"Bearer {token}"}).json()["id"]
        return User(id=user_id, username=username, password=password, token=token)
