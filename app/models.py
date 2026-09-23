from datetime import date

from sqlalchemy import Column, Integer, String, Float, Boolean, Date

from .database import Base

class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)


class Transaction(Base):

    __tablename__ = "transactions"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )


    type = Column(
        String,
        nullable=False
    )


    category = Column(
        String,
        nullable=False
    )


    amount = Column(
        Float,
        nullable=False
    )


    description = Column(
        String,
        nullable=True
    )


    date = Column(
        Date,
        nullable=False
    )


    recurring_id = Column(
        Integer,
        nullable=True
    )
    
# =========================================================
# РЕГУЛЯРНЫЕ ПЛАТЕЖИ
# =========================================================

class RecurringTransaction(Base):

    __tablename__ = "recurring_transactions"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )


    name = Column(
        String,
        nullable=False
    )


    amount = Column(
        Float,
        nullable=False
    )


    type = Column(
        String,
        nullable=False
    )


    category = Column(
        String,
        nullable=False
    )


    frequency = Column(
        String,
        default="monthly"
    )


    day_of_month = Column(
        Integer,
        nullable=False
    )


    next_payment_date = Column(
        Date,
        nullable=False
    )


    is_active = Column(
        Boolean,
        default=True
    )


    description = Column(
        String,
        default=""
    )

# =========================================================
# УВЕДОМЛЕНИЯ
# =========================================================

class Notification(Base):

    __tablename__ = "notifications"


    id = Column(
        Integer,
        primary_key=True,
        index=True
    )


    title = Column(
        String,
        nullable=False
    )


    message = Column(
        String,
        nullable=False
    )


    type = Column(
        String,
        default="info"
    )
    # info
    # warning
    # danger


    is_read = Column(
        Boolean,
        default=False
    )


    created_at = Column(
        Date,
        nullable=False
    )
    # =========================================================
# TELEGRAM ПОЛЬЗОВАТЕЛИ
# =========================================================

class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id = Column(Integer, primary_key=True, index=True)

    telegram_id = Column(
        Integer,
        unique=True,
        nullable=False,
        index=True
    )

    full_name = Column(
        String,
        nullable=False
    )

    role = Column(
        String,
        nullable=False,
        default="employee"
    )
    # director
    # employee

    is_active = Column(
        Boolean,
        default=True
    )


# =========================================================
# СОТРУДНИКИ И РАСХОДЫ
# =========================================================

class EmployeeExpense(Base):
    __tablename__ = "employee_expenses"

    id = Column(Integer, primary_key=True, index=True)

    telegram_user_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    transaction_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    description = Column(
        String,
        nullable=True
    )


# =========================================================
# ЧЕКИ
# =========================================================

class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(Integer, primary_key=True, index=True)

    transaction_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    telegram_user_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    filename = Column(
        String,
        nullable=False
    )

    filepath = Column(
        String,
        nullable=False
    )

    ocr_text = Column(
        String,
        nullable=True
    )

    shop_name = Column(
        String,
        nullable=True
    )

    receipt_amount = Column(
        Float,
        nullable=True
    )

    receipt_date = Column(
        Date,
        nullable=True
    )

    uploaded_at = Column(
        Date,
        default=date.today
    )