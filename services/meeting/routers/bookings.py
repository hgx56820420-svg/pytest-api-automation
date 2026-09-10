"""Meeting 预约路由：创建 / 取消 / 我的预约列表。

预约状态机：booked -> cancelled（cancelled 是终态）。

时间冲突规则：同一会议室存在 status=booked 且时间区间重叠的预约时返回 409，
不产生任何副作用。

成功副作用（同一事务）：
    user.balance_after = user.balance_before - hourly_price × 时长（小时）
    booking.status = booked, booking.amount = 折算金额快照
取消成功副作用：
    user.balance_after = user.balance_before + booking.amount
    booking.status = cancelled
任意失败不变量：余额和预约记录数均不得变化。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.meeting.auth import get_current_user
from services.meeting.database import get_db
from services.meeting.models import Booking, Room, User
from services.meeting.schemas import BookingCreate, BookingResponse, PaginatedBookings

router = APIRouter(prefix="/api/bookings", tags=["bookings"])


def _overlap_exists(db: Session, room_id: int, start, end) -> bool:
    """[start, end) 与既有 booked 预约是否重叠：start < other.end 且 end > other.start。"""
    existing = db.scalars(
        select(Booking).where(
            Booking.room_id == room_id,
            Booking.status == "booked",
            Booking.start_time < end,
            Booking.end_time > start,
        )
    ).all()
    return bool(existing)


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
def create_booking(payload: BookingCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = db.get(Room, payload.room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Room not found")
    if room.status != "active":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Room is disabled")
    if _overlap_exists(db, room.id, payload.start_time, payload.end_time):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Time slot conflict")

    hours = (payload.end_time - payload.start_time).total_seconds() / 3600
    amount = round(room.hourly_price * hours, 2)
    if current_user.balance < amount:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Balance is not enough")

    current_user.balance -= amount
    booking = Booking(
        user_id=current_user.id,
        room_id=room.id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        amount=amount,
        status="booked",
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


@router.post("/{booking_id}/cancel", response_model=BookingResponse)
def cancel_booking(booking_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    booking = db.get(Booking, booking_id)
    if booking is None or booking.user_id != current_user.id:
        # 他人预约同样返回 404，不泄露资源是否存在
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found")
    if booking.status != "booked":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Booking already cancelled")

    booking.status = "cancelled"
    current_user.balance += booking.amount
    db.commit()
    db.refresh(booking)
    return booking


@router.get("", response_model=PaginatedBookings)
def list_bookings(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(Booking).where(Booking.user_id == current_user.id))
    rows = db.scalars(
        select(Booking)
        .where(Booking.user_id == current_user.id)
        .order_by(Booking.id.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return PaginatedBookings(total=total, page=page, size=size, items=rows)
