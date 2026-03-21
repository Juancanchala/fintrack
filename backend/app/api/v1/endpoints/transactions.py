from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.dependencies import get_current_user, get_db
from app.models.models import User, Transaction, Account, TransactionType
from app.schemas.schemas import TransactionCreate, TransactionUpdate, TransactionOut

router = APIRouter(prefix="/transactions", tags=["transactions"])

def _adjust_balance(account: Account, tx_type: TransactionType, amount: float, reverse: bool = False):
    m = -1 if reverse else 1
    if tx_type == TransactionType.income:
        account.balance += m * amount
    elif tx_type == TransactionType.expense:
        account.balance -= m * amount

@router.get("", response_model=List[TransactionOut])
def list_transactions(
    transaction_type: Optional[TransactionType] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Transaction).filter(Transaction.user_id == current_user.id)
    if transaction_type:
        q = q.filter(Transaction.transaction_type == transaction_type)
    return q.order_by(Transaction.date.desc()).offset(offset).limit(limit).all()

@router.post("", response_model=TransactionOut, status_code=201)
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    account = db.query(Account).filter_by(id=payload.account_id, user_id=current_user.id).first()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
    data = payload.model_dump()
    data["user_id"] = current_user.id
    if not data.get("date"):
        data["date"] = datetime.utcnow()
    if payload.transaction_type == TransactionType.transfer:
        if not payload.destination_account_id:
            raise HTTPException(status_code=400, detail="destination_account_id required for transfers")
        dest = db.query(Account).filter_by(id=payload.destination_account_id, user_id=current_user.id).first()
        if not dest:
            raise HTTPException(status_code=404, detail="Destination account not found")
        account.balance -= payload.amount
        dest.balance += payload.amount
    else:
        _adjust_balance(account, payload.transaction_type, payload.amount)
    tx = Transaction(**data)
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx

@router.put("/{transaction_id}", response_model=TransactionOut)
def update_transaction(transaction_id: int, payload: TransactionUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tx = db.query(Transaction).filter_by(id=transaction_id, user_id=current_user.id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Revertir ajustes de balance anteriores
    old_account = db.query(Account).filter_by(id=tx.account_id).first()
    if tx.transaction_type == TransactionType.transfer:
        old_dest = db.query(Account).filter_by(id=tx.destination_account_id).first()
        if old_account:
            old_account.balance += tx.amount
        if old_dest:
            old_dest.balance -= tx.amount
    else:
        if old_account:
            _adjust_balance(old_account, tx.transaction_type, tx.amount, reverse=True)

    # Aplicar nuevos ajustes de balance
    new_account = db.query(Account).filter_by(id=payload.account_id, user_id=current_user.id).first()
    if not new_account:
        raise HTTPException(status_code=404, detail="Account not found")

    if payload.transaction_type == TransactionType.transfer:
        if not payload.destination_account_id:
            raise HTTPException(status_code=400, detail="destination_account_id required for transfers")
        new_dest = db.query(Account).filter_by(id=payload.destination_account_id, user_id=current_user.id).first()
        if not new_dest:
            raise HTTPException(status_code=404, detail="Destination account not found")
        new_account.balance -= payload.amount
        new_dest.balance += payload.amount
    else:
        _adjust_balance(new_account, payload.transaction_type, payload.amount)

    # Actualizar campos de la transacción
    for field, value in payload.model_dump().items():
        setattr(tx, field, value)
    if not tx.date:
        tx.date = datetime.utcnow()

    db.commit()
    db.refresh(tx)
    return tx

@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tx = db.query(Transaction).filter_by(id=transaction_id, user_id=current_user.id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found")
    account = db.query(Account).filter_by(id=tx.account_id).first()
    if account:
        _adjust_balance(account, tx.transaction_type, tx.amount, reverse=True)
    db.delete(tx)
    db.commit()

@router.get("/summary", response_model=dict)
def get_summary(year: int = Query(None), month: int = Query(None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    now = datetime.utcnow()
    year = year or now.year
    month = month or now.month
    date_from = datetime(year, month, 1)
    date_to = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    q = db.query(Transaction.transaction_type, func.sum(Transaction.amount).label("total"), func.count(Transaction.id).label("count")).filter(
        Transaction.user_id == current_user.id,
        Transaction.date >= date_from,
        Transaction.date < date_to,
    ).group_by(Transaction.transaction_type)
    results = {r.transaction_type: {"total": r.total, "count": r.count} for r in q.all()}
    income = results.get(TransactionType.income, {}).get("total", 0) or 0
    expense = results.get(TransactionType.expense, {}).get("total", 0) or 0
    return {"year": year, "month": month, "income": income, "expense": expense, "balance": income - expense,
            "income_count": results.get(TransactionType.income, {}).get("count", 0),
            "expense_count": results.get(TransactionType.expense, {}).get("count", 0)}

@router.get("/by-category", response_model=List[dict])
def get_by_category(year: int = Query(None), month: int = Query(None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.models.models import Category
    now = datetime.utcnow()
    year = year or now.year
    month = month or now.month
    date_from = datetime(year, month, 1)
    date_to = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
    rows = db.query(Category.name, Category.color, Category.icon, func.sum(Transaction.amount).label("total")).join(
        Transaction, Transaction.category_id == Category.id).filter(
        Transaction.user_id == current_user.id,
        Transaction.transaction_type == TransactionType.expense,
        Transaction.date >= date_from,
        Transaction.date < date_to,
    ).group_by(Category.id).order_by(func.sum(Transaction.amount).desc()).all()
    return [{"name": r.name, "color": r.color, "icon": r.icon, "total": r.total} for r in rows]

@router.get("/monthly-trend", response_model=List[dict])
def get_monthly_trend(months: int = Query(6), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from sqlalchemy import extract
    rows = db.query(extract("year", Transaction.date).label("year"), extract("month", Transaction.date).label("month"),
        Transaction.transaction_type, func.sum(Transaction.amount).label("total")).filter(
        Transaction.user_id == current_user.id).group_by("year", "month", Transaction.transaction_type).order_by("year", "month").all()
    trend = {}
    for r in rows:
        key = f"{int(r.year)}-{int(r.month):02d}"
        if key not in trend:
            trend[key] = {"period": key, "income": 0, "expense": 0}
        if r.transaction_type == TransactionType.income:
            trend[key]["income"] = r.total
        elif r.transaction_type == TransactionType.expense:
            trend[key]["expense"] = r.total
    return [trend[k] for k in sorted(trend.keys())[-months:]]
