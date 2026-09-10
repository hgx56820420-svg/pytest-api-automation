"""Library 图书路由：列表 / 创建 / 详情 / 更新 / 下架（软删除）。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from services.library.auth import get_current_user
from services.library.database import get_db
from services.library.models import Book, User
from services.library.schemas import BookCreate, BookResponse, BookUpdate, PaginatedBooks

router = APIRouter(prefix="/api/books", tags=["books"])


@router.get("", response_model=PaginatedBooks)
def list_books(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    total = len(db.scalars(select(Book)).all())
    rows = db.scalars(
        select(Book).order_by(Book.id.asc()).offset((page - 1) * size).limit(size)
    ).all()
    return PaginatedBooks(total=total, page=page, size=size, items=rows)


@router.post("", response_model=BookResponse, status_code=status.HTTP_201_CREATED)
def create_book(payload: BookCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    book = Book(
        title=payload.title,
        author=payload.author,
        deposit=payload.deposit,
        total_copies=payload.total_copies,
        available_copies=payload.total_copies,
        status="on_shelf",
    )
    db.add(book)
    db.commit()
    db.refresh(book)
    return book


@router.get("/{book_id}", response_model=BookResponse)
def get_book(book_id: int, db: Session = Depends(get_db)):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")
    return book


@router.put("/{book_id}", response_model=BookResponse)
def update_book(
    book_id: int,
    payload: BookUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(book, field, value)
    # available_copies 始终夹在 [0, total_copies] 区间内
    book.available_copies = min(book.available_copies, book.total_copies)
    db.commit()
    db.refresh(book)
    return book


@router.delete("/{book_id}", response_model=BookResponse)
def offline_book(book_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """下架是软删除：记录保留、状态 off_shelf、禁止再借。"""
    book = db.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")
    book.status = "off_shelf"
    db.commit()
    db.refresh(book)
    return book
