"""商品模块：列表 / 详情 / 创建 / 更新 / 下架。"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Product, User
from app.schemas import (
    ProductCreateRequest,
    ProductListResponse,
    ProductResponse,
    ProductUpdateRequest,
)

router = APIRouter(prefix="/api/products", tags=["products"])


def _get_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


@router.get("", response_model=ProductListResponse)
def list_products(
    db: Session = Depends(get_db),
    # ge/le 由 FastAPI 校验，非法分页参数直接 422，不会打到业务代码
    page: int = Query(default=1, ge=1),
    size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(on_sale|off_sale)$"),
):
    conditions = []
    if keyword:
        conditions.append(Product.name.like(f"%{keyword}%"))
    if status_filter:
        conditions.append(Product.status == status_filter)

    total = db.scalar(select(func.count()).select_from(Product).where(*conditions))
    items = db.scalars(
        select(Product)
        .where(*conditions)
        .order_by(Product.id)
        .offset((page - 1) * size)
        .limit(size)
    ).all()

    return ProductListResponse(total=total or 0, page=page, size=size, items=list(items))


@router.get("/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, product_id)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    product = Product(name=payload.name, price=payload.price, stock=payload.stock)
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.put("/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    payload: ProductUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    product = _get_or_404(db, product_id)
    # exclude_unset：只更新请求里真正传了的字段，没传的保持原值
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    db.refresh(product)
    return product


@router.delete("/{product_id}", response_model=ProductResponse)
def offline_product(
    product_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """软删除：只把状态改成 off_sale，不真的删记录。

    这样订单里的历史商品引用不会断，也让测试可以断言"下架后状态变了"
    而不是"记录消失了"。
    """
    product = _get_or_404(db, product_id)
    product.status = "off_sale"
    db.commit()
    db.refresh(product)
    return product
