from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Coupon, User
from app.schemas import CouponCreateRequest, CouponResponse

router = APIRouter(prefix="/api/coupons", tags=["coupons"])


@router.get("", response_model=list[CouponResponse])
def list_coupons(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.scalars(select(Coupon).where(Coupon.status == "active").order_by(Coupon.id)).all()


@router.post("", response_model=CouponResponse, status_code=status.HTTP_201_CREATED)
def create_coupon(payload: CouponCreateRequest, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if db.scalar(select(Coupon).where(Coupon.code == payload.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Coupon already exists")
    coupon = Coupon(**payload.model_dump())
    db.add(coupon)
    db.commit()
    db.refresh(coupon)
    return coupon
