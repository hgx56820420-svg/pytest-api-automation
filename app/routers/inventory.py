from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import InventoryTransaction, Product, User
from app.schemas import InventoryTransactionResponse

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.get("/{product_id}/transactions", response_model=list[InventoryTransactionResponse])
def transactions(product_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    if db.get(Product, product_id) is None:
        raise HTTPException(404, detail="Product not found")
    return db.scalars(select(InventoryTransaction).where(InventoryTransaction.product_id == product_id).order_by(InventoryTransaction.id)).all()
