"""Library 借阅路由：借书 / 归还 / 我的借阅列表。

借阅状态机：borrowed -> returned（returned 是终态）。

成功副作用（同一事务）：
    book.available_copies -= 1, user.deposit -= book.deposit, 新增 borrow 记录
归还成功副作用：
    book.available_copies += 1, user.deposit += borrow.deposit_charged, 状态 returned
任意失败不变量：副本数、押金和借阅记录数均不得变化。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from services.library.auth import get_current_user
from services.library.database import get_db
from services.library.models import Book, Borrow, User
from services.library.schemas import BorrowCreate, BorrowResponse, PaginatedBorrows

router = APIRouter(prefix="/api/borrows", tags=["borrows"])


@router.post("", response_model=BorrowResponse, status_code=status.HTTP_201_CREATED)
def create_borrow(payload: BorrowCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = db.get(Book, payload.book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")
    if book.status != "on_shelf":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Book is not on shelf")
    if book.available_copies < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No available copies")
    if current_user.deposit < book.deposit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Insufficient deposit")

    book.available_copies -= 1
    current_user.deposit -= book.deposit
    borrow = Borrow(
        user_id=current_user.id,
        book_id=book.id,
        deposit_charged=book.deposit,
        status="borrowed",
    )
    db.add(borrow)
    db.commit()
    db.refresh(borrow)
    return borrow


@router.post("/{borrow_id}/return", response_model=BorrowResponse)
def return_borrow(borrow_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    borrow = db.get(Borrow, borrow_id)
    if borrow is None or borrow.user_id != current_user.id:
        # 他人借阅记录同样返回 404，不泄露资源是否存在
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Borrow not found")
    if borrow.status != "borrowed":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Borrow already returned")

    borrow.status = "returned"
    book = db.get(Book, borrow.book_id)
    book.available_copies += 1
    current_user.deposit += borrow.deposit_charged
    db.commit()
    db.refresh(borrow)
    return borrow


@router.get("", response_model=PaginatedBorrows)
def list_borrows(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(Borrow).where(Borrow.user_id == current_user.id))
    rows = db.scalars(
        select(Borrow)
        .where(Borrow.user_id == current_user.id)
        .order_by(Borrow.id.asc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return PaginatedBorrows(total=total, page=page, size=size, items=rows)
