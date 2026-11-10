from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from examples.errors import errors
from examples.mini_shop.auth import get_current_admin, get_current_user
from examples.mini_shop.database import get_db
from examples.mini_shop.models import Coupon, User
from examples.mini_shop.schemas import CouponCreateRequest, CouponResponse

router = APIRouter(prefix="/api/coupons", tags=["coupons"])


@router.get("", response_model=list[CouponResponse], responses=errors(401))
def list_coupons(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(Coupon).where(Coupon.status == "active").order_by(Coupon.id)).all()


@router.post("", response_model=CouponResponse, status_code=status.HTTP_201_CREATED, responses=errors(401, 403, 409))
def create_coupon(payload: CouponCreateRequest, db: Session = Depends(get_db), _: User = Depends(get_current_admin)):
    # 业务逻辑放在不带装饰器的函数里，变异测试才能覆盖（见 orders.py）
    return add_coupon(db, payload)


def add_coupon(db: Session, payload: CouponCreateRequest) -> Coupon:
    if db.scalar(select(Coupon).where(Coupon.code == payload.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Coupon already exists")
    coupon = Coupon(**payload.model_dump())
    db.add(coupon)
    try:
        db.commit()
    except IntegrityError:
        # 并发创建同一 code：唯一约束兜底，返回和串行时一样的 409
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Coupon already exists")
    db.refresh(coupon)
    return coupon
