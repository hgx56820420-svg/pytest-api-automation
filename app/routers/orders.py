"""订单模块：下单 / 列表 / 详情 / 支付 / 取消。

这是整个被测服务里业务最"厚"的一块，也是测试最有料的一块：
- 下单要同时校验商品状态、库存、余额，任一不满足都要拒绝
- 下单成功会产生副作用（扣库存 + 扣余额）
- 取消要把副作用回滚回去
- 状态机限制了哪些操作在哪些状态下合法
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Order, Product, User
from app.schemas import OrderCreateRequest, OrderListResponse, OrderResponse

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _get_own_order_or_404(db: Session, order_id: int, user: User) -> Order:
    """取自己的订单。

    别人的订单也返回 404 而不是 403：不让攻击者通过状态码差异
    探测"这个 id 的订单是否存在"。
    """
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = db.get(Product, payload.product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")

    if product.status != "on_sale":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Product is not on sale")

    if product.stock < payload.quantity:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Insufficient stock")

    amount = round(product.price * payload.quantity, 2)
    if current_user.balance < amount:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Insufficient balance")

    # 扣库存 + 扣余额 + 建订单，放在同一个事务里，一起成功或一起失败
    product.stock -= payload.quantity
    current_user.balance = round(current_user.balance - amount, 2)
    order = Order(
        user_id=current_user.id,
        product_id=product.id,
        quantity=payload.quantity,
        amount=amount,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.get("", response_model=OrderListResponse)
def list_my_orders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    status_filter: str | None = Query(
        default=None, alias="status", pattern="^(created|paid|cancelled)$"
    ),
):
    conditions = [Order.user_id == current_user.id]
    if status_filter:
        conditions.append(Order.status == status_filter)

    total = db.scalar(select(func.count()).select_from(Order).where(*conditions))
    items = db.scalars(
        select(Order)
        .where(*conditions)
        .order_by(Order.id)
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    return OrderListResponse(total=total or 0, page=page, size=size, items=list(items))


@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_own_order_or_404(db, order_id, current_user)


@router.post("/{order_id}/pay", response_model=OrderResponse)
def pay_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = _get_own_order_or_404(db, order_id, current_user)
    # 409 表示"当前资源状态不允许这个操作"，这是状态机类接口的标准语义
    if order.status != "created":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Cannot pay an order in status '{order.status}'"
        )
    order.status = "paid"
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/cancel", response_model=OrderResponse)
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = _get_own_order_or_404(db, order_id, current_user)
    if order.status != "created":
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Cannot cancel an order in status '{order.status}'"
        )

    # 回滚下单时的副作用
    product = db.get(Product, order.product_id)
    if product is not None:
        product.stock += order.quantity
    current_user.balance = round(current_user.balance + order.amount, 2)
    order.status = "cancelled"

    db.commit()
    db.refresh(order)
    return order
