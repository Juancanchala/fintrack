from datetime import datetime, timedelta
import random
from sqlalchemy.orm import Session
from app.models.models import User, Account, Category, Transaction, Budget, TransactionType, BudgetPeriod
from app.core.security import get_password_hash

DEFAULT_EXPENSE_CATEGORIES = [
    ("Alimentación", "#ef4444", "🍔"),
    ("Transporte", "#f97316", "🚗"),
    ("Vivienda", "#eab308", "🏠"),
    ("Salud", "#22c55e", "💊"),
    ("Entretenimiento", "#8b5cf6", "🎬"),
    ("Ropa", "#ec4899", "👕"),
    ("Educación", "#06b6d4", "📚"),
    ("Servicios", "#64748b", "⚡"),
    ("Otros gastos", "#6b7280", "📦"),
]

DEFAULT_INCOME_CATEGORIES = [
    ("Salario", "#22c55e", "💰"),
    ("Freelance", "#10b981", "💻"),
    ("Inversiones", "#3b82f6", "📈"),
    ("Otros ingresos", "#6366f1", "✨"),
]

def seed_default_categories(db: Session, user_id: int):
    for name, color, icon in DEFAULT_EXPENSE_CATEGORIES:
        if not db.query(Category).filter_by(user_id=user_id, name=name).first():
            db.add(Category(user_id=user_id, name=name, transaction_type=TransactionType.expense, color=color, icon=icon, is_default=True))
    for name, color, icon in DEFAULT_INCOME_CATEGORIES:
        if not db.query(Category).filter_by(user_id=user_id, name=name).first():
            db.add(Category(user_id=user_id, name=name, transaction_type=TransactionType.income, color=color, icon=icon, is_default=True))
    db.commit()

def seed_demo_user(db: Session):
    if db.query(User).filter_by(email="demo@fintrack.app").first():
        return db.query(User).filter_by(email="demo@fintrack.app").first()
    user = User(email="demo@fintrack.app", username="demo", hashed_password=get_password_hash("demo1234"), full_name="Usuario Demo")
    db.add(user)
    db.flush()
    seed_default_categories(db, user.id)
    checking = Account(user_id=user.id, name="Cuenta Corriente", account_type="checking", balance=2500000, currency="COP", color="#6366f1", icon="🏦")
    savings = Account(user_id=user.id, name="Ahorros", account_type="savings", balance=8000000, currency="COP", color="#22c55e", icon="💰")
    cash = Account(user_id=user.id, name="Efectivo", account_type="cash", balance=150000, currency="COP", color="#f59e0b", icon="💵")
    db.add_all([checking, savings, cash])
    db.flush()
    categories = db.query(Category).filter_by(user_id=user.id).all()
    expense_cats = [c for c in categories if c.transaction_type == TransactionType.expense]
    salary_cat = next(c for c in categories if c.name == "Salario")
    now = datetime.utcnow()
    for month_offset in range(3):
        month_start = (now.replace(day=1) - timedelta(days=30 * month_offset)).replace(day=1)
        db.add(Transaction(user_id=user.id, account_id=checking.id, category_id=salary_cat.id, transaction_type=TransactionType.income, amount=4500000, description="Salario mensual", date=month_start))
        for _ in range(random.randint(15, 20)):
            cat = random.choice(expense_cats)
            db.add(Transaction(user_id=user.id, account_id=random.choice([checking.id, cash.id]), category_id=cat.id, transaction_type=TransactionType.expense, amount=round(random.uniform(15000, 450000), -3), description=f"Gasto en {cat.name.lower()}", date=month_start.replace(day=random.randint(1, 28))))
    food_cat = next(c for c in expense_cats if c.name == "Alimentación")
    db.add(Budget(user_id=user.id, category_id=food_cat.id, name="Presupuesto Alimentación", amount=600000, period=BudgetPeriod.monthly, start_date=now.replace(day=1)))
    db.commit()
    db.refresh(user)
    return user