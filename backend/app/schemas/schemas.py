from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, field_validator
from app.models.models import TransactionType, BudgetPeriod

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserCreate(BaseModel):
    email: EmailStr
    username: str
    password: str
    full_name: Optional[str] = None

class UserOut(BaseModel):
    id: int
    email: str
    username: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}

class AccountCreate(BaseModel):
    name: str
    account_type: str = "checking"
    balance: float = 0.0
    currency: str = "COP"
    color: Optional[str] = "#6366f1"
    icon: Optional[str] = "💳"

class AccountOut(BaseModel):
    id: int
    name: str
    account_type: str
    balance: float
    currency: str
    color: Optional[str]
    icon: Optional[str]
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}

class CategoryCreate(BaseModel):
    name: str
    transaction_type: TransactionType
    color: Optional[str] = "#6366f1"
    icon: Optional[str] = "📁"

class CategoryOut(BaseModel):
    id: int
    name: str
    transaction_type: TransactionType
    color: Optional[str]
    icon: Optional[str]
    is_default: bool
    model_config = {"from_attributes": True}

class TransactionCreate(BaseModel):
    account_id: int
    category_id: Optional[int] = None
    destination_account_id: Optional[int] = None
    transaction_type: TransactionType
    amount: float
    description: Optional[str] = None
    notes: Optional[str] = None
    date: Optional[datetime] = None
    is_recurring: bool = False

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v

class TransactionUpdate(BaseModel):
    account_id: int
    category_id: Optional[int] = None
    destination_account_id: Optional[int] = None
    transaction_type: TransactionType
    amount: float
    description: Optional[str] = None
    notes: Optional[str] = None
    date: Optional[datetime] = None
    is_recurring: bool = False

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v):
        if v <= 0:
            raise ValueError("Amount must be positive")
        return v

class TransactionOut(BaseModel):
    id: int
    account_id: int
    category_id: Optional[int]
    destination_account_id: Optional[int]
    transaction_type: TransactionType
    amount: float
    description: Optional[str]
    date: datetime
    is_recurring: bool
    created_at: datetime
    category: Optional[CategoryOut] = None
    account: Optional[AccountOut] = None
    model_config = {"from_attributes": True}

class BudgetCreate(BaseModel):
    category_id: int
    name: str
    amount: float
    period: BudgetPeriod = BudgetPeriod.monthly
    start_date: Optional[datetime] = None
    alert_threshold: float = 0.8

class BudgetOut(BaseModel):
    id: int
    category_id: int
    name: str
    amount: float
    period: BudgetPeriod
    start_date: datetime
    is_active: bool
    alert_threshold: float
    spent: float = 0.0
    remaining: float = 0.0
    percentage_used: float = 0.0
    category: Optional[CategoryOut] = None
    model_config = {"from_attributes": True}

class AIInsightRequest(BaseModel):
    question: Optional[str] = None

class AIInsightResponse(BaseModel):
    insight: str
    suggestions: List[str] = []