from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.database import get_db
from app.models import CartItem, Product, User
from app.schemas import CartItemRequest, CartResponse

router = APIRouter(prefix="/api/cart", tags=["cart"])


def _cart(db: Session, user: User):
    return db.scalars(select(CartItem).options(joinedload(CartItem.product)).where(CartItem.user_id == user.id).order_by(CartItem.id)).all()


@router.get("", response_model=CartResponse)
def get_cart(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return CartResponse(items=_cart(db, user))


@router.post("/items", response_model=CartResponse, status_code=status.HTTP_201_CREATED)
def add_cart_item(payload: CartItemRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    product = db.get(Product, payload.product_id)
    if product is None or product.status != "on_sale":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found or not on sale")
    item = db.scalar(select(CartItem).where(CartItem.user_id == user.id, CartItem.product_id == product.id))
    new_quantity = payload.quantity + (item.quantity if item else 0)
    if new_quantity > product.stock:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Insufficient stock")
    if item:
        item.quantity = new_quantity
    else:
        db.add(CartItem(user_id=user.id, product_id=product.id, quantity=new_quantity))
    db.commit()
    return CartResponse(items=_cart(db, user))


@router.put("/items/{product_id}", response_model=CartResponse)
def update_cart_item(product_id: int, payload: CartItemRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.scalar(select(CartItem).where(CartItem.user_id == user.id, CartItem.product_id == product_id))
    product = db.get(Product, product_id)
    if item is None or product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    if payload.product_id != product_id or payload.quantity > product.stock:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid cart quantity")
    item.quantity = payload.quantity
    db.commit()
    return CartResponse(items=_cart(db, user))


@router.delete("/items/{product_id}", response_model=CartResponse)
def remove_cart_item(product_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.scalar(select(CartItem).where(CartItem.user_id == user.id, CartItem.product_id == product_id))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    db.delete(item)
    db.commit()
    return CartResponse(items=_cart(db, user))


@router.delete("", response_model=CartResponse)
def clear_cart(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    for item in db.scalars(select(CartItem).where(CartItem.user_id == user.id)).all():
        db.delete(item)
    db.commit()
    return CartResponse(items=[])
