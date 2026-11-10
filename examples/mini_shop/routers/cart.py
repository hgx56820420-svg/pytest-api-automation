from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from examples.errors import errors
from examples.mini_shop.auth import get_current_user
from examples.mini_shop.database import get_db
from examples.mini_shop.models import CartItem, Product, User
from examples.mini_shop.schemas import CartItemRequest, CartResponse

router = APIRouter(prefix="/api/cart", tags=["cart"])


def _cart(db: Session, user: User):
    return db.scalars(select(CartItem).options(joinedload(CartItem.product)).where(CartItem.user_id == user.id).order_by(CartItem.id)).all()


@router.get("", response_model=CartResponse, responses=errors(401))
def get_cart(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return CartResponse(items=_cart(db, user))


def _add_quantity(db: Session, user: User, product: Product, quantity: int) -> bool:
    """在数据库里原子地加数量，加完不超过库存才生效。并发添加时不会互相覆盖。"""
    result = db.execute(
        update(CartItem)
        .where(
            CartItem.user_id == user.id,
            CartItem.product_id == product.id,
            CartItem.quantity + quantity <= product.stock,
        )
        .values(quantity=CartItem.quantity + quantity)
    )
    return result.rowcount == 1


@router.post("/items", response_model=CartResponse, status_code=status.HTTP_201_CREATED, responses=errors(400, 401, 404))
def add_cart_item(payload: CartItemRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 业务逻辑放在不带装饰器的函数里，变异测试才能覆盖（见 orders.py）
    return add_item(db, user, payload)


def add_item(db: Session, user: User, payload: CartItemRequest) -> CartResponse:
    product = db.get(Product, payload.product_id)
    if product is None or product.status != "on_sale":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found or not on sale")
    insufficient = HTTPException(status.HTTP_400_BAD_REQUEST, detail="Insufficient stock")
    item = db.scalar(select(CartItem).where(CartItem.user_id == user.id, CartItem.product_id == product.id))
    if item is not None:
        if not _add_quantity(db, user, product, payload.quantity):
            raise insufficient
    elif payload.quantity > product.stock:
        raise insufficient
    else:
        db.add(CartItem(user_id=user.id, product_id=product.id, quantity=payload.quantity))
        try:
            db.commit()
        except IntegrityError:
            # 另一个请求刚插入了同一行：改成在那一行上加数量
            db.rollback()
            if not _add_quantity(db, user, product, payload.quantity):
                raise insufficient
    db.commit()
    db.expire_all()
    return CartResponse(items=_cart(db, user))


@router.put("/items/{product_id}", response_model=CartResponse, responses=errors(400, 401, 404))
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


@router.delete("/items/{product_id}", response_model=CartResponse, responses=errors(401, 404))
def remove_cart_item(product_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    item = db.scalar(select(CartItem).where(CartItem.user_id == user.id, CartItem.product_id == product_id))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cart item not found")
    db.delete(item)
    db.commit()
    return CartResponse(items=_cart(db, user))


@router.delete("", response_model=CartResponse, responses=errors(401))
def clear_cart(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    for item in db.scalars(select(CartItem).where(CartItem.user_id == user.id)).all():
        db.delete(item)
    db.commit()
    return CartResponse(items=[])
