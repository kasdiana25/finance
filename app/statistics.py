from sqlalchemy.orm import Session
from sqlalchemy import func

from .models import Transaction


def get_statistics(db: Session):
    income = db.query(
        func.sum(Transaction.amount)
    ).filter(
        Transaction.type == "income"
    ).scalar() or 0

    expense = db.query(
        func.sum(Transaction.amount)
    ).filter(
        Transaction.type == "expense"
    ).scalar() or 0

    balance = income - expense

    return {
        "income": income,
        "expense": expense,
        "balance": balance
    }