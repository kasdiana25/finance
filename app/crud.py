from sqlalchemy.orm import Session
from . import models, schemas
from .models import RecurringTransaction


# =========================================================
# TRANSACTIONS
# =========================================================

def get_transactions(db: Session):
    return db.query(
        models.Transaction
    ).order_by(
        models.Transaction.date.desc()
    ).all()


def get_transaction(
    db: Session,
    transaction_id: int
):
    return db.query(
        models.Transaction
    ).filter(
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
    db_transaction = get_transaction(
        db,
        transaction_id
    )

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


def delete_transaction(
    db: Session,
    transaction_id: int
):
    db_transaction = get_transaction(
        db,
        transaction_id
    )

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
# FUTURE PAYMENTS
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


# =========================================================
# NOTIFICATIONS
# =========================================================

def get_notifications(db):
    return db.query(
        models.Notification
    ).order_by(
        models.Notification.created_at.desc()
    ).all()


# =========================================================
# MONEY TRANSFERS
# =========================================================

def get_money_transfers(db: Session):
    return db.query(
        models.MoneyTransfer
    ).order_by(
        models.MoneyTransfer.created_at.desc()
    ).all()


def get_money_transfer(
    db: Session,
    transfer_id: int
):
    return db.query(
        models.MoneyTransfer
    ).filter(
        models.MoneyTransfer.id == transfer_id
    ).first()


def create_money_transfer(
    db: Session,
    telegram_user_id: int,
    amount: float,
    purpose: str,
    comment: str = ""
):
    transfer = models.MoneyTransfer(
        telegram_user_id=telegram_user_id,
        amount=amount,
        purpose=purpose,
        comment=comment,
        status="issued"
    )

    db.add(transfer)
    db.commit()
    db.refresh(transfer)

    return transfer


def update_money_transfer_status(
    db: Session,
    transfer_id: int,
    status: str
):
    transfer = get_money_transfer(
        db,
        transfer_id
    )

    if transfer is None:
        return None

    transfer.status = status

    db.commit()
    db.refresh(transfer)

    return transfer


# =========================================================
# PURCHASE REQUESTS
# =========================================================

def get_purchase_requests(db: Session):
    return db.query(
        models.PurchaseRequest
    ).order_by(
        models.PurchaseRequest.id.desc()
    ).all()


def get_purchase_request(
    db: Session,
    request_id: int
):
    return db.query(
        models.PurchaseRequest
    ).filter(
        models.PurchaseRequest.id == request_id
    ).first()


def get_purchase_request_items(
    db: Session,
    request_id: int
):
    return db.query(
        models.PurchaseRequestItem
    ).filter(
        models.PurchaseRequestItem.request_id == request_id
    ).all()


def get_purchase_request_total(
    db: Session,
    request_id: int
):
    items = get_purchase_request_items(
        db,
        request_id
    )

    total = 0

    for item in items:

        if item.estimated_price is not None:
            total += (
                item.quantity *
                item.estimated_price
            )

    return total


def generate_purchase_request_number(
    db: Session
):
    last_request = db.query(
        models.PurchaseRequest
    ).order_by(
        models.PurchaseRequest.id.desc()
    ).first()

    if last_request is None:
        next_number = 1
    else:
        next_number = last_request.id + 1

    return f"З-{next_number:06d}"


def create_purchase_request(
    db: Session,
    telegram_user_id: int,
    title: str,
    description: str = None,
    category: str = None,
    priority: str = "normal",
    items: list = None
):
    request_number = generate_purchase_request_number(
        db
    )

    purchase_request = models.PurchaseRequest(
        number=request_number,
        telegram_user_id=telegram_user_id,
        title=title,
        description=description,
        category=category,
        priority=priority,
        status="new"
    )

    db.add(purchase_request)
    db.flush()

    if items:

        for item in items:

            request_item = models.PurchaseRequestItem(
                request_id=purchase_request.id,
                product_name=item["product_name"],
                quantity=item.get("quantity", 1),
                unit=item.get("unit", "шт."),
                estimated_price=item.get("estimated_price"),
                comment=item.get("comment")
            )

            db.add(request_item)

    db.commit()
    db.refresh(purchase_request)

    return purchase_request


def update_purchase_request_status(
    db: Session,
    request_id: int,
    status: str
):
    purchase_request = get_purchase_request(
        db,
        request_id
    )

    if purchase_request is None:
        return None

    allowed_statuses = [
        "new",
        "approved",
        "ordered",
        "received",
        "rejected"
    ]

    if status not in allowed_statuses:
        return None

    from datetime import date

    purchase_request.status = status

    if status == "approved":
        purchase_request.approved_at = date.today()

    elif status == "ordered":
        purchase_request.ordered_at = date.today()

    elif status == "received":
        purchase_request.received_at = date.today()

    db.commit()
    db.refresh(purchase_request)

    return purchase_request


# =========================================================
# PRODUCTS
# =========================================================

def get_products(db: Session):
    return db.query(
        models.Product
    ).order_by(
        models.Product.name
    ).all()


def get_product(
    db: Session,
    product_id: int
):
    return db.query(
        models.Product
    ).filter(
        models.Product.id == product_id
    ).first()


def generate_inventory_number(db: Session):
    """
    Генерирует следующий инвентарный номер:
    INV-0001
    INV-0002
    INV-0003
    """

    last_item = db.query(
        models.InventoryItem
    ).order_by(
        models.InventoryItem.id.desc()
    ).first()

    if last_item is None:
        next_number = 1
    else:
        next_number = last_item.id + 1

    return f"INV-{next_number:04d}"


def create_product(
    db: Session,
    name: str,
    category: str = None,
    unit: str = "шт.",
    description: str = None,
    is_inventory: bool = False,
    room: str = None
):
    product = models.Product(
        name=name,
        category=category,
        unit=unit,
        description=description,
        is_inventory=is_inventory,
        room=room
    )

    db.add(product)
    db.flush()

    if is_inventory:
        inventory_item = models.InventoryItem(
            inventory_number=generate_inventory_number(db),
            product_id=product.id,
            room=room,
            status="active"
        )
        db.add(inventory_item)

    db.commit()
    db.refresh(product)

    return product


def update_product(
    db: Session,
    product_id: int,
    name: str,
    category: str = None,
    unit: str = "шт.",
    description: str = None,
    is_inventory: bool = False,
    room: str = None
):
    product = get_product(db, product_id)

    if product is None:
        return None

    was_inventory = product.is_inventory

    product.name = name
    product.category = category
    product.unit = unit
    product.description = description
    product.is_inventory = is_inventory
    product.room = room

    if is_inventory and not was_inventory:
        inventory_item = models.InventoryItem(
            inventory_number=generate_inventory_number(db),
            product_id=product.id,
            room=room,
            status="active"
        )
        db.add(inventory_item)

    elif is_inventory and was_inventory:
        inventory_items = db.query(
            models.InventoryItem
        ).filter(
            models.InventoryItem.product_id == product.id
        ).all()

        for inventory_item in inventory_items:
            inventory_item.room = room

    elif not is_inventory and was_inventory:
        inventory_items = db.query(
            models.InventoryItem
        ).filter(
            models.InventoryItem.product_id == product.id
        ).all()

        for inventory_item in inventory_items:
            db.delete(inventory_item)

    db.commit()
    db.refresh(product)

    return product
    
    # =====================================================
    # ТОВАР СТАЛ ИНВЕНТАРНЫМ
    # =====================================================

    if is_inventory and not was_inventory:

        existing_inventory = db.query(
            models.InventoryItem
        ).filter(
            models.InventoryItem.product_id == product.id
        ).first()

        if existing_inventory is None:

            inventory_item = models.InventoryItem(
                inventory_number=generate_inventory_number(db),
                product_id=product.id,
                status="active"
            )

            db.add(inventory_item)

    # =====================================================
    # ТОВАР ПЕРЕСТАЛ БЫТЬ ИНВЕНТАРНЫМ
    # =====================================================

    elif not is_inventory and was_inventory:

        inventory_items = db.query(
            models.InventoryItem
        ).filter(
            models.InventoryItem.product_id == product.id
        ).all()

        for inventory_item in inventory_items:
            db.delete(inventory_item)

    db.commit()
    db.refresh(product)

    return product


def delete_product(
    db: Session,
    product_id: int
):
    product = get_product(
        db,
        product_id
    )

    if product is None:
        return None

    # Удаляем связанные записи инвентаризации
    inventory_items = db.query(
        models.InventoryItem
    ).filter(
        models.InventoryItem.product_id == product.id
    ).all()

    for inventory_item in inventory_items:
        db.delete(inventory_item)

    db.delete(product)

    db.commit()

    return product

# =========================================================
# EMPLOYEES
# =========================================================

def get_employees(db: Session):
    return db.query(
        models.TelegramUser
    ).order_by(
        models.TelegramUser.full_name
    ).all()


def get_employee(
    db: Session,
    employee_id: int
):
    return db.query(
        models.TelegramUser
    ).filter(
        models.TelegramUser.id == employee_id
    ).first()


def update_employee_status(
    db: Session,
    employee_id: int,
    is_active: bool
):
    employee = get_employee(
        db,
        employee_id
    )

    if employee is None:
        return None

    employee.is_active = is_active

    db.commit()
    db.refresh(employee)

    return employee


def update_employee_role(
    db: Session,
    employee_id: int,
    role: str
):
    employee = get_employee(
        db,
        employee_id
    )

    if employee is None:
        return None

    employee.role = role

    db.commit()
    db.refresh(employee)

    return employee


# =========================================================
# RECEIPTS
# =========================================================

def get_receipts(db: Session):
    return db.query(
        models.Receipt
    ).order_by(
        models.Receipt.id.desc()
    ).all()


def get_receipt(
    db: Session,
    receipt_id: int
):
    return db.query(
        models.Receipt
    ).filter(
        models.Receipt.id == receipt_id
    ).first()


# =========================================================
# REPORT STATISTICS
# =========================================================

def get_report_statistics(db: Session):

    from sqlalchemy import func

    total_income = db.query(
        func.coalesce(
            func.sum(models.Transaction.amount),
            0
        )
    ).filter(
        models.Transaction.type == "income"
    ).scalar()

    total_expense = db.query(
        func.coalesce(
            func.sum(models.Transaction.amount),
            0
        )
    ).filter(
        models.Transaction.type == "expense"
    ).scalar()

    balance = total_income - total_expense

    expenses_by_category = db.query(
        models.Transaction.category,
        func.sum(models.Transaction.amount)
    ).filter(
        models.Transaction.type == "expense"
    ).group_by(
        models.Transaction.category
    ).order_by(
        func.sum(models.Transaction.amount).desc()
    ).all()

    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance,
        "expenses_by_category": [
            {
                "category": category,
                "amount": amount
            }
            for category, amount in expenses_by_category
        ]
    }