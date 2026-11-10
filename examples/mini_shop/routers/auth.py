"""认证模块：注册 / 登录 / 查询当前用户。"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from examples.errors import errors
from examples.mini_shop.auth import create_access_token, get_current_user, hash_password, verify_password
from examples.mini_shop.config import DEFAULT_USER_BALANCE
from examples.mini_shop.database import get_db
from examples.mini_shop.models import User
from examples.mini_shop.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED, responses=errors(409))
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    exists = db.scalar(select(User).where(User.username == payload.username))
    if exists:
        # 409 而不是 400：语义上是"与现有资源冲突"
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Username already exists")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        balance=DEFAULT_USER_BALANCE,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 并发注册同名用户时，先查不存在的两个请求都会走到这里，靠唯一约束兜底
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Username already exists")
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse, responses=errors(401))
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    # 用户不存在和密码错误返回同样的信息，避免泄露"哪些用户名存在"
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserResponse, responses=errors(401))
def me(current_user: User = Depends(get_current_user)):
    return current_user
