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
    transaction_id = Column(Integer, nullable=True, index=True)
    telegram_user_id = Column(Integer, nullable=False, index=True)

    transfer_id = Column(Integer, nullable=True, index=True)

    filename = Column(String, nullable=False)
    filepath = Column(String, nullable=False)
    ocr_text = Column(String, nullable=True)
    shop_name = Column(String, nullable=True)
    receipt_amount = Column(Float, nullable=True)
    receipt_date = Column(Date, nullable=True)
    uploaded_at = Column(Date, default=date.today)

# =========================================================
# ВЫДАЧА ДЕНЕГ СОТРУДНИКАМ
# =========================================================

class MoneyTransfer(Base):
    __tablename__ = "money_transfers"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    telegram_user_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    amount = Column(
        Float,
        nullable=False
    )

    purpose = Column(
        String,
        nullable=False
    )

    comment = Column(
        String,
        nullable=True
    )

    status = Column(
        String,
        default="issued"
    )

    created_at = Column(
        Date,
        default=date.today
    )

# =========================================================
# ЗАЯВКИ НА ПОКУПКУ
# =========================================================

class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"

    id = Column(Integer, primary_key=True, index=True)

    number = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    telegram_user_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    title = Column(
        String,
        nullable=False
    )

    description = Column(
        String,
        nullable=True
    )

    category = Column(
        String,
        nullable=True
    )

    priority = Column(
        String,
        nullable=False,
        default="normal"
    )

    status = Column(
        String,
        nullable=False,
        default="new"
    )

    created_at = Column(
        Date,
        default=date.today
    )

    approved_at = Column(
        Date,
        nullable=True
    )

    ordered_at = Column(
        Date,
        nullable=True
    )

    received_at = Column(
        Date,
        nullable=True
    )


class PurchaseRequestItem(Base):
    __tablename__ = "purchase_request_items"

    id = Column(Integer, primary_key=True, index=True)

    request_id = Column(
        Integer,
        nullable=False,
        index=True
    )

    # Название требуемого товара
    product_name = Column(
        String,
        nullable=False
    )

    # Количество
    quantity = Column(
        Float,
        nullable=False,
        default=1
    )

    # Единица измерения
    unit = Column(
        String,
        nullable=True,
        default="шт."
    )

    # Предполагаемая цена
    estimated_price = Column(
        Float,
        nullable=True
    )

    # Комментарий по товару
    comment = Column(
        String,
        nullable=True
    )

# =========================================================
# ТОВАРЫ
# =========================================================

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)

    # Название товара
    name = Column(String, nullable=False, index=True)

    # Категория товара
    category = Column(String, nullable=True)

    # Единица измерения: шт., кг, л, упаковка и т.д.
    unit = Column(String, nullable=False, default="шт.")

    # Описание товара
    description = Column(String, nullable=True)

    # True — товар является имуществом и участвует
    # в инвентаризации.
    #
    # False — расходный материал.
    # В инвентаризацию такой товар НЕ попадает.
    is_inventory = Column(Boolean, nullable=False, default=False)

    room = Column(String, nullable=True)  # кабинет

    # Дата добавления товара
    created_at = Column(Date, default=date.today)


# =========================================================
# ИНВЕНТАРНЫЕ ОБЪЕКТЫ
# =========================================================

class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, index=True)

    # Уникальный инвентарный номер
    # Например: ИНВ-000001
    inventory_number = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    # ID товара из таблицы products
    product_id = Column(
        Integer,
        nullable=False,
        index=True
    )


    # ID ответственного сотрудника
    # Это ID записи TelegramUser, а не Telegram ID.
    responsible_user_id = Column(
        Integer,
        nullable=True,
        index=True
    )

    # Кабинет / помещение
    room = Column(
        String,
        nullable=True,
        index=True
    )

    # Дата покупки
    purchase_date = Column(
        Date,
        nullable=True
    )

    # Стоимость конкретного экземпляра
    purchase_price = Column(
        Float,
        nullable=True
    )

    # Состояние имущества:
    # active     — используется
    # damaged    — повреждено
    # written_off — списано
    # lost       — потеряно
    status = Column(
        String,
        nullable=False,
        default="active"
    )

    # Путь к QR-коду
    qr_code = Column(
        String,
        nullable=True
    )

    # Дата создания инвентарного объекта
    created_at = Column(
        Date,
        default=date.today
    )