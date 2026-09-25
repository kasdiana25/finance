from datetime import date
from typing import Optional, List


from pydantic import BaseModel, Field


class TransactionCreate(BaseModel):
    type: str = Field(..., pattern="^(income|expense)$")
    category: str
    amount: float = Field(..., gt=0)
    description: Optional[str] = None
    date: date


class TransactionResponse(TransactionCreate):
    id: int

    class Config:
        from_attributes = True

class RecurringTransactionCreate(BaseModel):

    name: str

    amount: float

    type: str

    category: str

    frequency: str = "monthly"

    day_of_month: int

    next_payment_date: date

    description: str = ""



class RecurringTransactionResponse(
    RecurringTransactionCreate
):

    id: int


    class Config:
        from_attributes = True

from datetime import date


class NotificationResponse(BaseModel):

    id: int

    title: str

    message: str

    type: str

    is_read: bool

    created_at: date


    class Config:
        from_attributes = True

# =========================================================
# ВЫДАЧА ДЕНЕГ СОТРУДНИКАМ
# =========================================================

class MoneyTransferCreate(BaseModel):

    telegram_user_id: int

    amount: float = Field(
        ...,
        gt=0
    )

    purpose: str

    comment: Optional[str] = None


class MoneyTransferResponse(
    MoneyTransferCreate
):

    id: int

    status: str

    created_at: date

    class Config:
        from_attributes = True

# =========================================================
# ЗАЯВКИ НА ПОКУПКУ
# =========================================================

class PurchaseRequestItemCreate(BaseModel):
    product_name: str

    quantity: float = Field(
        default=1,
        gt=0
    )

    unit: Optional[str] = "шт."

    estimated_price: Optional[float] = Field(
        default=None,
        ge=0
    )

    comment: Optional[str] = None


class PurchaseRequestCreate(BaseModel):
    telegram_user_id: int

    title: str

    description: Optional[str] = None

    category: Optional[str] = None

    priority: str = "normal"

    items: List[PurchaseRequestItemCreate]


class PurchaseRequestItemResponse(
    PurchaseRequestItemCreate
):
    id: int

    request_id: int

    class Config:
        from_attributes = True


class PurchaseRequestResponse(BaseModel):
    id: int

    number: str

    telegram_user_id: int

    title: str

    description: Optional[str] = None

    category: Optional[str] = None

    priority: str

    status: str

    created_at: date

    approved_at: Optional[date] = None

    ordered_at: Optional[date] = None

    received_at: Optional[date] = None

    items: List[PurchaseRequestItemResponse] = []

    class Config:
        from_attributes = True

# =========================================================
# ТОВАРЫ
# =========================================================

class ProductCreate(BaseModel):
    name: str
    category: Optional[str] = None
    unit: str = "шт."
    description: Optional[str] = None
    is_inventory: bool = False
    room: str | None = None


class ProductResponse(ProductCreate):
    id: int
    created_at: date

    class Config:
        from_attributes = True

# =========================================================
# СОТРУДНИКИ
# =========================================================

class TelegramUserResponse(BaseModel):
    id: int
    telegram_id: int
    full_name: str
    role: str
    is_active: bool

    class Config:
        from_attributes = True


# =========================================================
# ЧЕКИ
# =========================================================

class ReceiptResponse(BaseModel):
    id: int
    transaction_id: int
    telegram_user_id: int
    transfer_id: Optional[int] = None
    filename: str
    filepath: str
    ocr_text: Optional[str] = None
    shop_name: Optional[str] = None
    receipt_amount: Optional[float] = None
    receipt_date: Optional[date] = None
    uploaded_at: date

    class Config:
        from_attributes = True