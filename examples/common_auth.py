"""三个演示服务共用的鉴权：密码哈希、JWT、当前用户 / 管理员依赖、管理员种子账号。

每个服务创建自己的 TokenAuth，传入自己的密钥和 audience（aud），
所以一个服务签发的 token 在另一个服务里会被拒绝。

密码哈希用标准库的 pbkdf2_hmac，避免 bcrypt 在 Windows 上装轮子踩坑。
生产项目更推荐 argon2 / bcrypt。
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

_ITERATIONS = 100_000
_ALGORITHM = "HS256"

# auto_error=False：没带 Authorization 头时不让 FastAPI 直接抛 403，我们自己抛 401
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
    except (ValueError, TypeError):
        return False
    # 用 compare_digest 而不是 ==，避免时序侧信道
    return hmac.compare_digest(digest.hex(), digest_hex)


def load_secret(env_name: str) -> str:
    """读取 JWT 密钥。没配置时每次启动随机生成：代码里不存在默认密钥，代价是重启后旧 token 失效。"""
    return os.getenv(env_name) or secrets.token_urlsafe(32)


class TokenAuth:
    def __init__(self, *, secret: str, audience: str, user_model, get_db, expire_minutes: int = 60):
        self.secret = secret
        self.audience = audience
        self.user_model = user_model
        self.get_db = get_db
        self.expire_minutes = expire_minutes

    def create_token(self, user_id: int) -> str:
        expire = datetime.now(timezone.utc) + timedelta(minutes=self.expire_minutes)
        payload = {"sub": str(user_id), "aud": self.audience, "exp": expire}
        return jwt.encode(payload, self.secret, algorithm=_ALGORITHM)

    def current_user_dependency(self):
        """需要登录的接口依赖它。任何鉴权失败统一 401：缺 token / 签名错 / 过期 / aud 不符 / 用户不存在。"""

        def get_current_user(
            credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
            db: Session = Depends(self.get_db),
        ):
            unauthorized = HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
            if credentials is None:
                raise unauthorized
            try:
                payload = jwt.decode(
                    credentials.credentials, self.secret, algorithms=[_ALGORITHM], audience=self.audience
                )
                user_id = int(payload["sub"])
            except (jwt.PyJWTError, KeyError, ValueError):
                raise unauthorized
            user = db.get(self.user_model, user_id)
            if user is None:
                raise unauthorized
            return user

        return get_current_user

    def admin_dependency(self, get_current_user):
        """管理类接口依赖它：已登录但不是管理员返回 403。"""

        def get_current_admin(user=Depends(get_current_user)):
            if not user.is_admin:
                raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin only")
            return user

        return get_current_admin


def seed_admin(session_factory, user_model, env_prefix: str, **extra_fields) -> None:
    """按环境变量 {prefix}_ADMIN_USERNAME / {prefix}_ADMIN_PASSWORD 创建管理员；没配置就不创建。"""
    username = os.getenv(f"{env_prefix}_ADMIN_USERNAME")
    password = os.getenv(f"{env_prefix}_ADMIN_PASSWORD")
    if not username or not password:
        return
    with session_factory() as db:
        if db.scalar(select(user_model).where(user_model.username == username)) is not None:
            return
        db.add(user_model(username=username, password_hash=hash_password(password), is_admin=True, **extra_fields))
        db.commit()
