"""Meeting 会议室路由：列表 / 创建 / 详情 / 更新 / 停用（软删除）。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.meeting.auth import get_current_user
from services.meeting.database import get_db
from services.meeting.models import Room, User
from services.meeting.schemas import PaginatedRooms, RoomCreate, RoomResponse, RoomUpdate

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.get("", response_model=PaginatedRooms)
def list_rooms(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    total = len(db.scalars(select(Room)).all())
    rows = db.scalars(select(Room).order_by(Room.id.asc()).offset((page - 1) * size).limit(size)).all()
    return PaginatedRooms(total=total, page=page, size=size, items=rows)


@router.post("", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
def create_room(payload: RoomCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    room = Room(name=payload.name, capacity=payload.capacity, hourly_price=payload.hourly_price, status="active")
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


@router.get("/{room_id}", response_model=RoomResponse)
def get_room(room_id: int, db: Session = Depends(get_db)):
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Room not found")
    return room


@router.put("/{room_id}", response_model=RoomResponse)
def update_room(
    room_id: int,
    payload: RoomUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Room not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(room, field, value)
    db.commit()
    db.refresh(room)
    return room


@router.delete("/{room_id}", response_model=RoomResponse)
def disable_room(room_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """停用是软删除：记录保留、状态 disabled、禁止再预约。"""
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Room not found")
    room.status = "disabled"
    db.commit()
    db.refresh(room)
    return room
