from datetime import date
from typing import Optional


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