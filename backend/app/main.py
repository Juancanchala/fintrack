import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user, get_db
from app.db.database import create_tables, SessionLocal
from app.db.seed import seed_demo_user
from app.models.models import User, Transaction, Account, Category, TransactionType
from app.schemas.schemas import AIInsightRequest, AIInsightResponse

from app.api.v1.endpoints import auth, transactions, accounts_categories, budgets


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    db = SessionLocal()
    try:
        seed_demo_user(db)
    finally:
        db.close()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Personal Finance Tracker API — track income, expenses, budgets & get AI insights.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API Routes ────────────────────────────────────────────────────────────────

API_PREFIX = "/api/v1"

app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(transactions.router, prefix=API_PREFIX)
app.include_router(accounts_categories.router, prefix=API_PREFIX)
app.include_router(budgets.router, prefix=API_PREFIX)


# ── AI Insights ───────────────────────────────────────────────────────────────

@app.post(f"{API_PREFIX}/ai/insights", response_model=AIInsightResponse)
async def ai_insights(
    payload: AIInsightRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from openai import OpenAI
    from datetime import datetime
    from sqlalchemy import func

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY.startswith("sk-proj-pon"):
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")

    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Collect financial context
    accounts = db.query(Account).filter_by(user_id=current_user.id, is_active=True).all()
    total_balance = sum(a.balance for a in accounts)

    income_q = db.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == current_user.id,
        Transaction.transaction_type == TransactionType.income,
        Transaction.date >= month_start,
    ).scalar() or 0

    expense_q = db.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == current_user.id,
        Transaction.transaction_type == TransactionType.expense,
        Transaction.date >= month_start,
    ).scalar() or 0

    category_rows = (
        db.query(Category.name, func.sum(Transaction.amount).label("total"))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(
            Transaction.user_id == current_user.id,
            Transaction.transaction_type == TransactionType.expense,
            Transaction.date >= month_start,
        )
        .group_by(Category.id)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(5)
        .all()
    )

    top_cats = "\n".join([f"  - {r.name}: ${r.total:,.0f} COP" for r in category_rows]) or "  (sin datos)"

    financial_context = f"""
Usuario: {current_user.full_name or current_user.username}
Mes actual ({now.strftime('%B %Y')}):
  - Balance total en cuentas: ${total_balance:,.0f} COP
  - Ingresos del mes: ${income_q:,.0f} COP
  - Gastos del mes: ${expense_q:,.0f} COP
  - Saldo neto: ${income_q - expense_q:,.0f} COP
  - Tasa de ahorro: {((income_q - expense_q) / income_q * 100) if income_q > 0 else 0:.1f}%
Top categorías de gasto:
{top_cats}
"""

    user_question = payload.question or "Analiza mis finanzas y dame consejos personalizados para este mes."

    system_prompt = """Eres FinTrack AI, asesor financiero personal para Colombia. Responde ÚNICAMENTE con JSON válido:
{
  "insight": "string con el análisis estructurado",
  "suggestions": ["sugerencia 1", "sugerencia 2", "sugerencia 3"]
}

El campo "insight" debe seguir EXACTAMENTE esta estructura con saltos de línea reales (\\n):
📊 RESUMEN DEL MES\\n[una línea con los 3 datos clave: ingresos, gastos, saldo neto]\\n\\n[emoji] ANÁLISIS\\n[2-3 oraciones concretas sobre la situación financiera]\\n\\n[emoji] DESTACADO\\n[1 observación importante sobre la categoría con más gasto o el patrón más relevante]

Cada sugerencia en "suggestions" debe empezar con un emoji relevante y ser accionable en menos de 15 palabras.
Usa COP. Sé directo y alentador."""

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    import json
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=1000,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": f"Datos financieros:\n{financial_context}\n\nPregunta: {user_question}"},
        ]
    )

    text = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(text)
        return AIInsightResponse(
            insight=parsed.get("insight", text),
            suggestions=parsed.get("suggestions", []),
        )
    except json.JSONDecodeError:
        return AIInsightResponse(insight=text, suggestions=[])


# ── AI Chat ───────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    message: str
    history: list = []

class ChatResponse(BaseModel):
    reply: str
    action: Optional[str] = None
    transaction_created: Optional[dict] = None
    account_created: Optional[dict] = None
    budget_created: Optional[dict] = None
    category_created: Optional[dict] = None

@app.post(f"{API_PREFIX}/ai/chat", response_model=ChatResponse)
async def ai_chat(
    payload: ChatMessage,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from openai import OpenAI
    from datetime import datetime
    from sqlalchemy import func

    if not settings.OPENAI_API_KEY or settings.OPENAI_API_KEY.startswith("sk-proj-pon"):
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # Contexto financiero actual
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    accounts = db.query(Account).filter_by(user_id=current_user.id, is_active=True).all()
    categories = db.query(Category).filter_by(user_id=current_user.id).all()

    total_balance = sum(a.balance for a in accounts)

    income_q = db.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == current_user.id,
        Transaction.transaction_type == TransactionType.income,
        Transaction.date >= month_start,
    ).scalar() or 0

    expense_q = db.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == current_user.id,
        Transaction.transaction_type == TransactionType.expense,
        Transaction.date >= month_start,
    ).scalar() or 0

    category_rows = (
        db.query(Category.name, func.sum(Transaction.amount).label("total"))
        .join(Transaction, Transaction.category_id == Category.id)
        .filter(
            Transaction.user_id == current_user.id,
            Transaction.transaction_type == TransactionType.expense,
            Transaction.date >= month_start,
        )
        .group_by(Category.id)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )

    accounts_info = "\n".join([f"  - {a.name} ({a.account_type}): ${a.balance:,.0f} COP" for a in accounts])
    categories_expense = [c for c in categories if c.transaction_type.value == "expense"]
    categories_income = [c for c in categories if c.transaction_type.value == "income"]
    cats_expense_str = ", ".join([f"{c.icon}{c.name}(id:{c.id})" for c in categories_expense])
    cats_income_str = ", ".join([f"{c.icon}{c.name}(id:{c.id})" for c in categories_income])
    top_cats = "\n".join([f"  - {r.name}: ${r.total:,.0f} COP" for r in category_rows]) or "  Sin gastos aún"
    default_account_id = accounts[0].id if accounts else None

    system_prompt = f"""Eres el asistente financiero de FinTrack para {current_user.full_name or current_user.username}.

CONTEXTO FINANCIERO ACTUAL ({now.strftime('%B %Y')}):
Cuentas:
{accounts_info}
Balance total: ${total_balance:,.0f} COP
Ingresos del mes: ${income_q:,.0f} COP
Gastos del mes: ${expense_q:,.0f} COP
Saldo neto: ${income_q - expense_q:,.0f} COP

Top gastos por categoría:
{top_cats}

Categorías de gasto disponibles: {cats_expense_str}
Categorías de ingreso disponibles: {cats_income_str}
Cuenta por defecto id: {default_account_id}

INSTRUCCIONES:
Analiza el mensaje del usuario y responde ÚNICAMENTE con JSON válido según la intención:

1. REGISTRAR GASTO/INGRESO (ej: "Rappi 45000", "taxi 12000", "me pagaron 500000"):
{{
  "action": "create_transaction",
  "reply": "✅ Registrando: [descripción] por $[monto]",
  "transaction": {{
    "amount": 45000,
    "transaction_type": "expense",
    "category_id": 1,
    "account_id": {default_account_id},
    "description": "Rappi",
    "date": "{now.isoformat()}"
  }}
}}

2. CREAR CUENTA (ej: "crea una cuenta de ahorros", "nueva cuenta Nequi con 500000"):
{{
  "action": "create_account",
  "reply": "✅ Creando cuenta [nombre]",
  "account": {{
    "name": "Nequi",
    "account_type": "savings",
    "balance": 500000,
    "currency": "COP",
    "icon": "💳",
    "color": "#6366f1"
  }}
}}
account_type puede ser: checking, savings, cash, investment, credit

3. CREAR PRESUPUESTO (ej: "crea presupuesto de alimentación por 500000"):
{{
  "action": "create_budget",
  "reply": "✅ Creando presupuesto de [categoría] por $[monto]",
  "budget": {{
    "name": "Alimentación mensual",
    "category_id": 1,
    "amount": 500000,
    "period": "monthly",
    "alert_threshold": 0.8
  }}
}}

4. CREAR CATEGORÍA (ej: "agrega categoría Mascota de gasto"):
{{
  "action": "create_category",
  "reply": "✅ Creando categoría [nombre]",
  "category": {{
    "name": "Mascota",
    "transaction_type": "expense",
    "icon": "🐾",
    "color": "#6366f1"
  }}
}}
transaction_type: "expense" o "income"

5. PREGUNTA sobre finanzas:
{{
  "action": "answer",
  "reply": "respuesta con emojis. Usa viñetas (•) para listas, máximo 4 ítems."
}}

6. NO ESTÁ CLARO:
{{
  "action": "clarify",
  "reply": "🤔 pregunta corta para clarificar"
}}

Reglas: emoji al inicio del reply, viñetas • para listas, máximo 5 líneas, datos en COP, contexto colombiano."""

    import json

    messages = [{"role": "system", "content": system_prompt}]
    for h in payload.history[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": payload.message})

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=500,
        response_format={"type": "json_object"},
        messages=messages,
    )

    text = response.choices[0].message.content.strip()

    try:
        parsed = json.loads(text)
        action = parsed.get("action", "answer")
        reply = parsed.get("reply", "No entendí, ¿puedes repetir?")
        transaction_created = None

        if action == "create_transaction" and "transaction" in parsed:
            tx_data = parsed["transaction"]
            account = db.query(Account).filter_by(id=tx_data.get("account_id"), user_id=current_user.id).first()
            if not account:
                account = accounts[0] if accounts else None

            if account:
                tx_type = TransactionType(tx_data.get("transaction_type", "expense"))
                amount = float(tx_data.get("amount", 0))
                if amount > 0:
                    new_tx = Transaction(
                        user_id=current_user.id,
                        account_id=account.id,
                        category_id=tx_data.get("category_id"),
                        transaction_type=tx_type,
                        amount=amount,
                        description=tx_data.get("description", payload.message),
                        date=datetime.utcnow(),
                    )
                    if tx_type == TransactionType.income:
                        account.balance += amount
                    else:
                        account.balance -= amount
                    db.add(new_tx)
                    db.commit()
                    db.refresh(new_tx)
                    transaction_created = {
                        "id": new_tx.id,
                        "amount": amount,
                        "type": tx_type.value,
                        "description": new_tx.description,
                    }

        account_created = None
        budget_created = None
        category_created = None

        if action == "create_account" and "account" in parsed:
            from app.models.models import AccountType
            a_data = parsed["account"]
            try:
                acc_type = a_data.get("account_type", "checking")
                new_acc = Account(
                    user_id=current_user.id,
                    name=a_data.get("name", "Nueva cuenta"),
                    account_type=acc_type,
                    balance=float(a_data.get("balance", 0)),
                    currency=a_data.get("currency", "COP"),
                    icon=a_data.get("icon", "💳"),
                    color=a_data.get("color", "#6366f1"),
                )
                db.add(new_acc)
                db.commit()
                db.refresh(new_acc)
                account_created = {"id": new_acc.id, "name": new_acc.name, "balance": new_acc.balance}
            except Exception:
                reply = "⚠️ No pude crear la cuenta. Intenta desde la pestaña Cuentas."

        elif action == "create_budget" and "budget" in parsed:
            from app.models.models import Budget, BudgetPeriod as BP
            b_data = parsed["budget"]
            try:
                cat_id = b_data.get("category_id")
                cat = db.query(Category).filter_by(id=cat_id, user_id=current_user.id).first() if cat_id else None
                if not cat:
                    exp_cats = [c for c in categories if c.transaction_type.value == "expense"]
                    cat = exp_cats[0] if exp_cats else None
                if cat:
                    new_budget = Budget(
                        user_id=current_user.id,
                        category_id=cat.id,
                        name=b_data.get("name", f"Presupuesto {cat.name}"),
                        amount=float(b_data.get("amount", 0)),
                        period=BP(b_data.get("period", "monthly")),
                        alert_threshold=float(b_data.get("alert_threshold", 0.8)),
                        start_date=datetime.utcnow(),
                    )
                    db.add(new_budget)
                    db.commit()
                    db.refresh(new_budget)
                    budget_created = {"id": new_budget.id, "name": new_budget.name, "amount": new_budget.amount}
                else:
                    reply = "⚠️ No encontré la categoría. Créala primero o especifica el nombre."
            except Exception:
                reply = "⚠️ No pude crear el presupuesto. Intenta desde la pestaña Presupuestos."

        elif action == "create_category" and "category" in parsed:
            c_data = parsed["category"]
            try:
                tx_type = TransactionType(c_data.get("transaction_type", "expense"))
                new_cat = Category(
                    user_id=current_user.id,
                    name=c_data.get("name", "Nueva categoría"),
                    transaction_type=tx_type,
                    icon=c_data.get("icon", "📁"),
                    color=c_data.get("color", "#6366f1"),
                    is_default=False,
                )
                db.add(new_cat)
                db.commit()
                db.refresh(new_cat)
                category_created = {"id": new_cat.id, "name": new_cat.name, "icon": new_cat.icon}
            except Exception:
                reply = "⚠️ No pude crear la categoría. Intenta desde la pestaña Categorías."

        return ChatResponse(
            reply=reply, action=action,
            transaction_created=transaction_created,
            account_created=account_created,
            budget_created=budget_created,
            category_created=category_created,
        )

    except Exception as e:
        return ChatResponse(reply="Hubo un error procesando tu mensaje. Intenta de nuevo.", action="error")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}


# ── Frontend static files ─────────────────────────────────────────────────────

FRONTEND_DIR = Path("/app/frontend/pages")

if FRONTEND_DIR.exists():
    @app.get("/", response_class=HTMLResponse)
    async def serve_index():
        index = FRONTEND_DIR / "index.html"
        return HTMLResponse(content=index.read_text(encoding="utf-8"))

    @app.get("/dashboard", response_class=HTMLResponse)
    async def serve_dashboard():
        dash = FRONTEND_DIR / "dashboard.html"
        return HTMLResponse(content=dash.read_text(encoding="utf-8"))