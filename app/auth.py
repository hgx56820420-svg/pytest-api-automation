"""密码哈希与 JWT 鉴权。

密码哈希用标准库的 pbkdf2_hmac，避免 bcrypt 在 Windows 上装轮子踩坑。
生产项目更推荐 argon2 / bcrypt，这里的选择是为了让你换电脑也能一键跑起来。
"""

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY
from app.database import get_db
from app.models import User

_ITERATIONS = 100_000

# auto_error=False：没带 Authorization 头时不让 FastAPI 直接抛 403，
# 我们自己抛 401，这样测试断言的状态码更符合语义。
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    # 用 compare_digest 而不是 ==，避免时序侧信道
    return hmac.compare_digest(digest.hex(), digest_hex)


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """需要登录的接口都依赖这个函数。

    任何鉴权失败统一返回 401，这是接口测试里最常断言的一组用例：
    不带 token / token 格式错 / token 签名错 / token 过期 / 用户已不存在。
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized

    user = db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user
