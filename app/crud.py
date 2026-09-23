from sqlalchemy.orm import Session
from . import models, schemas
from .models import RecurringTransaction

def get_transactions(db: Session):
    return db.query(models.Transaction).order_by(
        models.Transaction.date.desc()
    ).all()


def get_transaction(db: Session, transaction_id: int):
    return db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id
    ).first()


def create_transaction(
    db: Session,
    transaction: schemas.TransactionCreate
):
    db_transaction = models.Transaction(
        type=transaction.type,
        category=transaction.category,
        amount=transaction.amount,
        description=transaction.description,
        date=transaction.date
    )

    db.add(db_transaction)
    db.commit()
    db.refresh(db_transaction)

    return db_transaction


def update_transaction(
    db: Session,
    transaction_id: int,
    transaction: schemas.TransactionCreate
):
    db_transaction = get_transaction(db, transaction_id)

    if db_transaction is None:
        return None

    db_transaction.type = transaction.type
    db_transaction.category = transaction.category
    db_transaction.amount = transaction.amount
    db_transaction.description = transaction.description
    db_transaction.date = transaction.date

    db.commit()
    db.refresh(db_transaction)

    return db_transaction


def delete_transaction(db: Session, transaction_id: int):
    db_transaction = get_transaction(db, transaction_id)

    if db_transaction is None:
        return None

    db.delete(db_transaction)
    db.commit()

    return db_transaction

# =========================================================
# REGULAR PAYMENTS
# =========================================================


def get_recurring_transactions(db):

    return db.query(
        RecurringTransaction
    ).all()



def create_recurring_transaction(
        db,
        data
):

    payment = RecurringTransaction(

        name=data.name,

        amount=data.amount,

        type=data.type,

        category=data.category,

        frequency=data.frequency,

        day_of_month=data.day_of_month,

        next_payment_date=data.next_payment_date,

        description=data.description
    )


    db.add(payment)

    db.commit()

    db.refresh(payment)


    return payment


# =========================================================
# БУДУЩИЕ ПЛАТЕЖИ
# =========================================================

def get_future_transactions(db):

    from datetime import date


    return db.query(
        models.RecurringTransaction
    ).filter(
        models.RecurringTransaction.next_payment_date >= date.today()
    ).order_by(
        models.RecurringTransaction.next_payment_date
    ).all()

def get_notifications(db):

    return db.query(
        models.Notification
    ).order_by(
        models.Notification.created_at.desc()
    ).all()