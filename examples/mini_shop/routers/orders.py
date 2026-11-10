"""订单模块：下单 / 列表 / 详情 / 支付 / 取消。

- 下单：校验商品、库存、优惠券，扣库存、占用优惠券次数，不扣余额
- 支付：created → paid，按订单金额扣余额
- 取消：created → cancelled，恢复库存和优惠券次数（未支付，不涉及退款）
- paid / cancelled 是终态，再支付或取消返回 409

并发安全：库存、余额、优惠券次数和订单状态都用"带条件的 UPDATE"修改，
条件不满足（库存不够、状态已变）时影响行数为 0，请求失败，不会超卖或重复扣款。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from examples.errors import errors
from examples.mini_shop.auth import get_current_user
from examples.mini_shop.database import get_db
from examples.mini_shop.models import CartItem, Coupon, InventoryTransaction, Order, Product, User
from examples.mini_shop.schemas import OrderCreateRequest, OrderListResponse, OrderResponse

router = APIRouter(prefix="/api/orders", tags=["orders"])


def _get_own_order_or_404(db: Session, order_id: int, user: User) -> Order:
    """取自己的订单。管理员也只能操作自己的订单。

    别人的订单也返回 404 而不是 403：不让攻击者通过状态码差异
    探测"这个 id 的订单是否存在"。
    """
    order = db.get(Order, order_id)
    if order is None or order.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


def _fail(db: Session, code: int, detail: str):
    db.rollback()
    raise HTTPException(code, detail=detail)


def _conflict(db: Session, order: Order, action: str):
    db.rollback()
    db.refresh(order)  # 读最新状态：可能是并发请求刚改的
    raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot {action} an order in status '{order.status}'")


def _transition(db: Session, order_id: int, to_status: str) -> bool:
    """created → to_status。另一个请求已经改过状态时返回 False。"""
    result = db.execute(
        update(Order).where(Order.id == order_id, Order.status == "created").values(status=to_status)
    )
    return result.rowcount == 1


# 路由函数只负责参数和依赖注入，业务逻辑放在不带装饰器的函数里：
# mutmut 不变异带装饰器的函数，这样核心逻辑才能被变异测试覆盖。


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED, responses=errors(400, 401, 404))
def create_order(
    payload: OrderCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return place_order(db, current_user, payload)


def place_order(db: Session, current_user: User, payload: OrderCreateRequest) -> Order:
    product = db.get(Product, payload.product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    if product.status != "on_sale":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Product is not on sale")
    if product.stock < payload.quantity:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Insufficient stock")

    amount = round(product.price * payload.quantity, 2)
    coupon = None
    if payload.coupon_code:
        coupon = db.scalar(select(Coupon).where(Coupon.code == payload.coupon_code, Coupon.status == "active"))
        if coupon is None or coupon.used_count >= coupon.max_uses:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid or exhausted coupon")
        amount = round(amount * (1 - coupon.discount_percent / 100), 2)

    # 上面是快速失败；下面的条件 UPDATE 才是并发下的真正保证
    reserved = db.execute(
        update(Product)
        .where(Product.id == product.id, Product.status == "on_sale", Product.stock >= payload.quantity)
        .values(stock=Product.stock - payload.quantity)
    )
    if reserved.rowcount != 1:
        _fail(db, status.HTTP_400_BAD_REQUEST, "Insufficient stock")
    if coupon:
        used = db.execute(
            update(Coupon)
            .where(Coupon.id == coupon.id, Coupon.status == "active", Coupon.used_count < Coupon.max_uses)
            .values(
                used_count=Coupon.used_count + 1,
                status=case((Coupon.used_count + 1 >= Coupon.max_uses, "exhausted"), else_=Coupon.status),
            )
        )
        if used.rowcount != 1:
            _fail(db, status.HTTP_400_BAD_REQUEST, "Invalid or exhausted coupon")

    order = Order(
        user_id=current_user.id,
        product_id=product.id,
        quantity=payload.quantity,
        amount=amount,
        coupon_code=coupon.code if coupon else None,
    )
    db.add(order)
    db.flush()  # 拿到 order.id，库存流水才能关联订单
    db.add(
        InventoryTransaction(
            product_id=product.id, quantity_change=-payload.quantity, reason="order_created", reference_id=order.id
        )
    )
    cart_item = db.scalar(select(CartItem).where(CartItem.user_id == current_user.id, CartItem.product_id == product.id))
    if cart_item:
        db.delete(cart_item)
    db.commit()
    db.refresh(order)
    return order


@router.get("", response_model=OrderListResponse, responses=errors(401))
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


@router.get("/{order_id}", response_model=OrderResponse, responses=errors(401, 404))
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_own_order_or_404(db, order_id, current_user)


@router.post("/{order_id}/pay", response_model=OrderResponse, responses=errors(400, 401, 404, 409))
def pay_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return pay(db, current_user, order_id)


def pay(db: Session, current_user: User, order_id: int) -> Order:
    order = _get_own_order_or_404(db, order_id, current_user)
    # 409 表示"当前资源状态不允许这个操作"，这是状态机类接口的标准语义
    if not _transition(db, order.id, "paid"):
        _conflict(db, order, "pay")
    charged = db.execute(
        update(User)
        .where(User.id == current_user.id, User.balance >= order.amount)
        .values(balance=func.round(User.balance - order.amount, 2))
    )
    if charged.rowcount != 1:
        _fail(db, status.HTTP_400_BAD_REQUEST, "Insufficient balance")
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/cancel", response_model=OrderResponse, responses=errors(401, 404, 409))
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return cancel(db, current_user, order_id)


def cancel(db: Session, current_user: User, order_id: int) -> Order:
    order = _get_own_order_or_404(db, order_id, current_user)
    if not _transition(db, order.id, "cancelled"):
        _conflict(db, order, "cancel")

    # 恢复下单时占用的库存和优惠券次数；订单未支付，余额不用动
    db.execute(update(Product).where(Product.id == order.product_id).values(stock=Product.stock + order.quantity))
    db.add(
        InventoryTransaction(
            product_id=order.product_id, quantity_change=order.quantity, reason="order_cancelled", reference_id=order.id
        )
    )
    if order.coupon_code:
        db.execute(
            update(Coupon)
            .where(Coupon.code == order.coupon_code, Coupon.used_count > 0)
            .values(
                used_count=Coupon.used_count - 1,
                # 只把"用完"的券恢复成可用，不覆盖其他状态
                status=case((Coupon.status == "exhausted", "active"), else_=Coupon.status),
            )
        )
    db.commit()
    db.refresh(order)
    return order
