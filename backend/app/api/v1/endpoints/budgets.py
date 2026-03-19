from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.dependencies import get_current_user, get_db
from app.models.models import User, Budget, Transaction, TransactionType
from app.schemas.schemas import BudgetCreate, BudgetOut

router = APIRouter(prefix="/budgets", tags=["budgets"])

def _compute_spent(db: Session, budget: Budget, user_id: int) -> float:
    now = datetime.utcnow()
    if budget.period.value == "monthly":
        date_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif budget.period.value == "weekly":
        from datetime import timedelta
        date_from = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        date_from = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return db.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == user_id,
        Transaction.category_id == budget.category_id,
        Transaction.transaction_type == TransactionType.expense,
        Transaction.date >= date_from,
    ).scalar() or 0.0

def _enrich(budget: Budget, spent: float) -> BudgetOut:
    out = BudgetOut.model_validate(budget)
    out.spent = spent
    out.remaining = max(budget.amount - spent, 0)
    out.percentage_used = round((spent / budget.amount * 100) if budget.amount > 0 else 0, 2)
    return out

@router.get("", response_model=List[BudgetOut])
def list_budgets(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    budgets = db.query(Budget).filter_by(user_id=current_user.id, is_active=True).all()
    return [_enrich(b, _compute_spent(db, b, current_user.id)) for b in budgets]

@router.post("", response_model=BudgetOut, status_code=201)
def create_budget(payload: BudgetCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    data = payload.model_dump()
    if not data.get("start_date"):
        data["start_date"] = datetime.utcnow().replace(day=1)
    data["user_id"] = current_user.id
    budget = Budget(**data)
    db.add(budget)
    db.commit()
    db.refresh(budget)
    return _enrich(budget, _compute_spent(db, budget, current_user.id))

@router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    budget = db.query(Budget).filter_by(id=budget_id, user_id=current_user.id).first()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    budget.is_active = False
    db.commit()
