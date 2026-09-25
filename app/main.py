from typing import List

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Request,
    Form
)

from fastapi.responses import (
    HTMLResponse,
    RedirectResponse
)

from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy.orm import Session
from pathlib import Path

from .database import Base, engine, get_db
from . import models, schemas, crud

from .statistics import get_statistics
from .recurring_service import generate_recurring_transactions
from .forecast import get_financial_forecast
from .notification_service import generate_notifications


# =========================================================
# DATABASE
# =========================================================

Base.metadata.create_all(bind=engine)


# =========================================================
# APP
# =========================================================

app = FastAPI(
    title="Finance Max Alice",
    description="Система автоматизации учета доходов и расходов",
    version="1.0.0"
)


# =========================================================
# АВТОРИЗАЦИЯ ДИРЕКТОРА
# =========================================================

DIRECTOR_LOGIN = "director"
DIRECTOR_PASSWORD = "123456"


# =========================================================
# ПРОВЕРКА АВТОРИЗАЦИИ
# =========================================================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):

    # Эти страницы доступны без авторизации
    if request.url.path in ["/login", "/logout"]:
        return await call_next(request)

    # Статические файлы доступны без авторизации
    if request.url.path.startswith("/static"):
        return await call_next(request)

    # Проверяем авторизацию
    if not request.session.get("director_authenticated"):
        return RedirectResponse(
            url="/login",
            status_code=303
        )

    return await call_next(request)


# =========================================================
# SESSION MIDDLEWARE
# =========================================================

app.add_middleware(
    SessionMiddleware,
    secret_key="finance-max-alice-director-secret-key-2026",
    session_cookie="finance_max_alice_session",
    max_age=60 * 60 * 8,
    same_site="lax",
    https_only=False
)


# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent


# =========================================================
# TEMPLATES
# =========================================================

templates = Jinja2Templates(
    directory=BASE_DIR / "templates"
)


# =========================================================
# STATIC
# =========================================================

app.mount(
    "/static",
    StaticFiles(
        directory=BASE_DIR / "static"
    ),
    name="static"
)


# =========================================================
# LOGIN
# =========================================================

@app.get(
    "/login",
    response_class=HTMLResponse
)
def login_page(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "error": None
        }
    )


# =========================================================
# LOGIN POST
# =========================================================

@app.post(
    "/login",
    response_class=HTMLResponse
)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):

    if (
        username == DIRECTOR_LOGIN
        and password == DIRECTOR_PASSWORD
    ):

        # Создаём сессию директора
        request.session["director_authenticated"] = True
        request.session["director_role"] = "director"

        return RedirectResponse(
            url="/home",
            status_code=303
        )

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "error": "Неверный логин или пароль"
        },
        status_code=401
    )


# =========================================================
# LOGOUT
# =========================================================

@app.get("/logout")
def logout(request: Request):

    request.session.clear()

    return RedirectResponse(
        url="/login",
        status_code=303
    )


# =========================================================
# ROOT
# =========================================================

@app.get("/")
def root():

    return RedirectResponse(
        url="/home",
        status_code=303
    )

# =========================================================
# API TRANSACTIONS
# =========================================================

@app.get(
    "/api/transactions",
    response_model=List[schemas.TransactionResponse]
)
def get_transactions_api(
    db: Session = Depends(get_db)
):
    return crud.get_transactions(db)


@app.get(
    "/api/transactions/{transaction_id}",
    response_model=schemas.TransactionResponse
)
def get_transaction_api(
    transaction_id: int,
    db: Session = Depends(get_db)
):
    transaction = crud.get_transaction(
        db,
        transaction_id
    )

    if transaction is None:
        raise HTTPException(
            status_code=404,
            detail="Операция не найдена"
        )

    return transaction


@app.post(
    "/api/transactions",
    response_model=schemas.TransactionResponse
)
def create_transaction_api(
    transaction: schemas.TransactionCreate,
    db: Session = Depends(get_db)
):
    return crud.create_transaction(
        db,
        transaction
    )


@app.put(
    "/api/transactions/{transaction_id}",
    response_model=schemas.TransactionResponse
)
def update_transaction_api(
    transaction_id: int,
    transaction: schemas.TransactionCreate,
    db: Session = Depends(get_db)
):
    result = crud.update_transaction(
        db,
        transaction_id,
        transaction
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Операция не найдена"
        )

    return result


@app.delete(
    "/api/transactions/{transaction_id}"
)
def delete_transaction_api(
    transaction_id: int,
    db: Session = Depends(get_db)
):
    result = crud.delete_transaction(
        db,
        transaction_id
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Операция не найдена"
        )

    return {
        "message": "Операция удалена"
    }


# =========================================================
# API STATISTICS
# =========================================================

@app.get("/api/statistics")
def statistics_api(
    db: Session = Depends(get_db)
):
    return get_statistics(db)


# =========================================================
# API RECURRING
# =========================================================

@app.get(
    "/api/recurring",
    response_model=List[
        schemas.RecurringTransactionResponse
    ]
)
def get_recurring_api(
    db: Session = Depends(get_db)
):
    return crud.get_recurring_transactions(db)


@app.post(
    "/api/recurring",
    response_model=schemas.RecurringTransactionResponse
)
def create_recurring_api(
    payment: schemas.RecurringTransactionCreate,
    db: Session = Depends(get_db)
):
    return crud.create_recurring_transaction(
        db,
        payment
    )


# =========================================================
# API CALENDAR
# =========================================================

@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(
    request: Request,
    db: Session = Depends(get_db)
):
    payments = crud.get_future_transactions(db)

    import calendar
    from datetime import date

    today = date.today()

    month_names = [
        "",
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь"
    ]

    month_name = month_names[today.month]

    days = []

    days_count = calendar.monthrange(
        today.year,
        today.month
    )[1]

    for number in range(1, days_count + 1):

        day_payments = []

        for payment in payments:

            if (
                payment.next_payment_date
                and payment.next_payment_date.day == number
            ):
                day_payments.append(payment)

        days.append({
            "number": number,
            "payments": day_payments
        })

    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={
            "days": days,
            "month": f"{month_name} {today.year}",
            "payments": payments
        }
    )


# =========================================================
# API FORECAST
# =========================================================

@app.get("/api/forecast")
def forecast_api(
    db: Session = Depends(get_db)
):
    return get_financial_forecast(db)


# =========================================================
# API TELEGRAM USERS
# =========================================================

@app.get("/api/telegram-users")
def get_telegram_users(
    db: Session = Depends(get_db)
):
    return db.query(
        models.TelegramUser
    ).filter(
        models.TelegramUser.is_active == True
    ).all()


# =========================================================
# API PRODUCTS
# =========================================================

@app.get(
    "/api/products",
    response_model=List[schemas.ProductResponse]
)
def get_products_api(
    db: Session = Depends(get_db)
):
    return crud.get_products(db)


@app.get(
    "/api/products/{product_id}",
    response_model=schemas.ProductResponse
)
def get_product_api(
    product_id: int,
    db: Session = Depends(get_db)
):
    product = crud.get_product(
        db,
        product_id
    )

    if product is None:
        raise HTTPException(
            status_code=404,
            detail="Товар не найден"
        )

    return product


@app.post(
    "/api/products",
    response_model=schemas.ProductResponse
)
def create_product_api(
    product: schemas.ProductCreate,
    db: Session = Depends(get_db)
):
    return crud.create_product(
        db=db,
        name=product.name,
        category=product.category,
        unit=product.unit,
        description=product.description,
        is_inventory=product.is_inventory,
        room=product.room
    )


@app.put(
    "/api/products/{product_id}",
    response_model=schemas.ProductResponse
)
def update_product_api(
    product_id: int,
    product: schemas.ProductCreate,
    db: Session = Depends(get_db)
):
    updated_product = crud.update_product(
        db=db,
        product_id=product_id,
        name=product.name,
        category=product.category,
        unit=product.unit,
        description=product.description,
        is_inventory=product.is_inventory,
        room=product.room   
    )

    if updated_product is None:
        raise HTTPException(
            status_code=404,
            detail="Товар не найден"
        )

    return updated_product


@app.delete(
    "/api/products/{product_id}"
)
def delete_product_api(
    product_id: int,
    db: Session = Depends(get_db)
):
    product = crud.delete_product(
        db,
        product_id
    )

    if product is None:
        raise HTTPException(
            status_code=404,
            detail="Товар не найден"
        )

    return {
        "message": "Товар удалён",
        "id": product_id
    }


# =========================================================
# HTML PRODUCTS
# =========================================================

@app.get(
    "/products",
    response_class=HTMLResponse
)
def products_page(
    request: Request,
    db: Session = Depends(get_db)
):
    products = crud.get_products(db)

    return templates.TemplateResponse(
        request=request,
        name="products.html",
        context={
            "products": products
        }
    )


# =========================================================
# API MONEY TRANSFERS
# =========================================================

@app.get(
    "/api/money-transfers",
    response_model=List[schemas.MoneyTransferResponse]
)
def get_money_transfers_api(
    db: Session = Depends(get_db)
):
    return crud.get_money_transfers(db)


@app.post(
    "/api/money-transfers",
    response_model=schemas.MoneyTransferResponse
)
def create_money_transfer_api(
    transfer: schemas.MoneyTransferCreate,
    db: Session = Depends(get_db)
):
    return crud.create_money_transfer(
        db=db,
        telegram_user_id=transfer.telegram_user_id,
        amount=transfer.amount,
        purpose=transfer.purpose,
        comment=transfer.comment
    )


# =========================================================
# HTML MONEY TRANSFERS
# =========================================================

@app.get(
    "/money-transfers",
    response_class=HTMLResponse
)
def money_transfers_page(
    request: Request,
    db: Session = Depends(get_db)
):
    employees = db.query(
        models.TelegramUser
    ).filter(
        models.TelegramUser.role == "employee",
        models.TelegramUser.is_active == True
    ).order_by(
        models.TelegramUser.full_name
    ).all()

    transfers_db = crud.get_money_transfers(db)

    transfers = []

    for transfer in transfers_db:

        employee = db.query(
            models.TelegramUser
        ).filter(
            models.TelegramUser.id == transfer.telegram_user_id
        ).first()

        transfers.append({
            "id": transfer.id,
            "employee_name": (
                employee.full_name
                if employee
                else "Неизвестный сотрудник"
            ),
            "amount": transfer.amount,
            "purpose": transfer.purpose,
            "comment": transfer.comment,
            "status": transfer.status,
            "created_at": transfer.created_at
        })

    return templates.TemplateResponse(
        request=request,
        name="money_transfers.html",
        context={
            "employees": employees,
            "transfers": transfers
        }
    )


@app.post("/money-transfers")
def create_money_transfer_page(
    telegram_user_id: int = Form(...),
    amount: float = Form(...),
    purpose: str = Form(...),
    comment: str = Form(""),
    db: Session = Depends(get_db)
):
    crud.create_money_transfer(
        db=db,
        telegram_user_id=telegram_user_id,
        amount=amount,
        purpose=purpose,
        comment=comment
    )

    return RedirectResponse(
        url="/money-transfers",
        status_code=303
    )


# =========================================================
# CLOSE MONEY TRANSFER
# =========================================================

@app.post(
    "/money-transfers/{transfer_id}/close"
)
def close_money_transfer(
    transfer_id: int,
    db: Session = Depends(get_db)
):
    transfer = crud.update_money_transfer_status(
        db,
        transfer_id,
        "closed"
    )

    if transfer is None:
        raise HTTPException(
            status_code=404,
            detail="Выдача денег не найдена"
        )

    return RedirectResponse(
        url="/money-transfers",
        status_code=303
    )


# =========================================================
# API PURCHASE REQUESTS
# =========================================================

@app.post(
    "/api/purchase-requests",
    response_model=schemas.PurchaseRequestResponse
)
def create_purchase_request_api(
    request_data: schemas.PurchaseRequestCreate,
    db: Session = Depends(get_db)
):
    items = []

    for item in request_data.items:

        items.append({
            "product_name": item.product_name,
            "quantity": item.quantity,
            "unit": item.unit,
            "estimated_price": item.estimated_price,
            "comment": item.comment
        })

    purchase_request = crud.create_purchase_request(
        db=db,
        telegram_user_id=request_data.telegram_user_id,
        title=request_data.title,
        description=request_data.description,
        category=request_data.category,
        priority=request_data.priority,
        items=items
    )

    return purchase_request


@app.get(
    "/api/purchase-requests",
    response_model=List[
        schemas.PurchaseRequestResponse
    ]
)
def get_purchase_requests_api(
    db: Session = Depends(get_db)
):
    return crud.get_purchase_requests(db)


@app.get(
    "/api/purchase-requests/{request_id}"
)
def get_purchase_request_api(
    request_id: int,
    db: Session = Depends(get_db)
):
    purchase_request = crud.get_purchase_request(
        db,
        request_id
    )

    if purchase_request is None:
        raise HTTPException(
            status_code=404,
            detail="Заявка не найдена"
        )

    items = crud.get_purchase_request_items(
        db,
        request_id
    )

    return {
        "id": purchase_request.id,
        "number": purchase_request.number,
        "telegram_user_id": purchase_request.telegram_user_id,
        "title": purchase_request.title,
        "description": purchase_request.description,
        "category": purchase_request.category,
        "priority": purchase_request.priority,
        "status": purchase_request.status,
        "created_at": purchase_request.created_at,
        "approved_at": purchase_request.approved_at,
        "ordered_at": purchase_request.ordered_at,
        "received_at": purchase_request.received_at,

        "items": [
            {
                "id": item.id,
                "request_id": item.request_id,
                "product_name": item.product_name,
                "quantity": item.quantity,
                "unit": item.unit,
                "estimated_price": item.estimated_price,
                "comment": item.comment
            }
            for item in items
        ]
    }
# =========================================================
# ИЗМЕНЕНИЕ СТАТУСА ЗАЯВКИ
# =========================================================

@app.post(
    "/purchase-requests/{request_id}/status"
)
def change_purchase_request_status(
    request_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db)
):
    purchase_request = crud.update_purchase_request_status(
        db,
        request_id,
        status
    )

    if purchase_request is None:
        raise HTTPException(
            status_code=404,
            detail="Заявка не найдена или статус недопустим"
        )

    return RedirectResponse(
        url="/purchase-requests",
        status_code=303
    )

# =========================================================
# HTML PURCHASE REQUESTS
# =========================================================

@app.get("/purchase-requests", response_class=HTMLResponse)

def purchase_requests_page(
    request: Request,
    db: Session = Depends(get_db)
):
    purchase_requests_db = crud.get_purchase_requests(db)

    employees = db.query(
        models.TelegramUser
    ).filter(
        models.TelegramUser.is_active == True
    ).order_by(
        models.TelegramUser.full_name
    ).all()

    purchase_requests = []

    for purchase_request in purchase_requests_db:

        items = crud.get_purchase_request_items(
            db,
            purchase_request.id
        )

        total = 0

        for item in items:

            if item.estimated_price is not None:

                total += (
                    item.quantity *
                    item.estimated_price
                )

        purchase_requests.append({
            "id": purchase_request.id,
            "number": purchase_request.number,
            "telegram_user_id": purchase_request.telegram_user_id,
            "title": purchase_request.title,
            "description": purchase_request.description,
            "category": purchase_request.category,
            "priority": purchase_request.priority,
            "status": purchase_request.status,
            "created_at": (
                purchase_request.created_at.isoformat()
                if purchase_request.created_at
                else None
            ),
            "approved_at": (
                purchase_request.approved_at.isoformat()
                if purchase_request.approved_at
                else None
            ),
            "ordered_at": (
                purchase_request.ordered_at.isoformat()
                if purchase_request.ordered_at
                else None
            ),
            "received_at": (
                purchase_request.received_at.isoformat()
                if purchase_request.received_at
                else None
            ),
            "total": total
        })

    return templates.TemplateResponse(
        request=request,
        name="purchase_requests.html",
        context={
            "purchase_requests": purchase_requests,
            "employees": employees
        }
    )


# =========================================================
# API NOTIFICATIONS
# =========================================================

@app.get(
    "/api/notifications",
    response_model=List[
        schemas.NotificationResponse
    ]
)
def notifications_api(
    db: Session = Depends(get_db)
):
    generate_notifications(db)

    return crud.get_notifications(db)


# =========================================================
# HTML NOTIFICATIONS
# =========================================================

@app.get(
    "/notifications-page",
    response_class=HTMLResponse
)
def notifications_page(
    request: Request,
    db: Session = Depends(get_db)
):
    generate_notifications(db)

    notifications = crud.get_notifications(db)

    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={
            "notifications": notifications
        }
    )


# =========================================================
# HTML RECEIPTS
# =========================================================

@app.get(
    "/receipts",
    response_class=HTMLResponse
)
def receipts_page(
    request: Request,
    db: Session = Depends(get_db)
):
    receipts = crud.get_receipts(db)

    employees = {
        employee.telegram_id: employee.full_name
        for employee in db.query(
            models.TelegramUser
        ).all()
    }

    return templates.TemplateResponse(
        request=request,
        name="receipts.html",
        context={
            "receipts": receipts,
            "employees": employees
        }
    )


# =========================================================
# HTML EMPLOYEES
# =========================================================

@app.get(
    "/employees",
    response_class=HTMLResponse
)
def employees_page(
    request: Request,
    db: Session = Depends(get_db)
):
    employees = crud.get_employees(db)

    return templates.TemplateResponse(
        request=request,
        name="employees.html",
        context={
            "employees": employees
        }
    )


# =========================================================
# EMPLOYEE STATUS
# =========================================================

@app.post(
    "/employees/{employee_id}/status"
)
def change_employee_status(
    employee_id: int,
    is_active: bool = Form(...),
    db: Session = Depends(get_db)
):
    employee = crud.update_employee_status(
        db,
        employee_id,
        is_active
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Сотрудник не найден"
        )

    return RedirectResponse(
        url="/employees",
        status_code=303
    )


# =========================================================
# EMPLOYEE ROLE
# =========================================================

@app.post(
    "/employees/{employee_id}/role"
)
def change_employee_role(
    employee_id: int,
    role: str = Form(...),
    db: Session = Depends(get_db)
):
    if role not in [
        "employee",
        "director"
    ]:
        raise HTTPException(
            status_code=400,
            detail="Недопустимая роль"
        )

    employee = crud.update_employee_role(
        db,
        employee_id,
        role
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Сотрудник не найден"
        )

    return RedirectResponse(
        url="/employees",
        status_code=303
    )


# =========================================================
# REPORTS
# =========================================================

@app.get(
    "/reports",
    response_class=HTMLResponse
)
def reports_page(
    request: Request,
    db: Session = Depends(get_db)
):
    statistics = crud.get_report_statistics(db)

    transactions = crud.get_transactions(db)

    transfers = crud.get_money_transfers(db)

    receipts = crud.get_receipts(db)

    purchase_requests = crud.get_purchase_requests(db)

    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "statistics": statistics,
            "transactions": transactions,
            "transfers": transfers,
            "receipts": receipts,
            "purchase_requests": purchase_requests
        }
    )


# =========================================================
# HTML HOME
# =========================================================

@app.get(
    "/home",
    response_class=HTMLResponse
)
def home(
    request: Request,
    db: Session = Depends(get_db)
):
    generate_recurring_transactions(db)

    transactions = crud.get_transactions(db)

    statistics = get_statistics(db)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "transactions": transactions[:10],
            "statistics": statistics
        }
    )


# =========================================================
# HTML TRANSACTIONS
# =========================================================

@app.get(
    "/transactions-page",
    response_class=HTMLResponse
)
def transactions_page(
    request: Request,
    db: Session = Depends(get_db)
):
    transactions = crud.get_transactions(db)

    return templates.TemplateResponse(
        request=request,
        name="transactions.html",
        context={
            "transactions": transactions
        }
    )


# =========================================================
# ADD TRANSACTION
# =========================================================
# Этот маршрут оставляем для совместимости.
# Основное добавление теперь выполняется через
# модальное окно на /transactions-page.
# =========================================================

@app.get(
    "/transactions/add",
    response_class=HTMLResponse
)
def add_transaction_page(
    request: Request
):
    return templates.TemplateResponse(
        request=request,
        name="add_transaction.html",
        context={}
    )


@app.post(
    "/transactions/add"
)
def add_transaction_form(
    type: str = Form(...),
    category: str = Form(...),
    amount: float = Form(...),
    date: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db)
):
    transaction = schemas.TransactionCreate(
        type=type,
        category=category,
        amount=amount,
        date=date,
        description=description
    )

    crud.create_transaction(
        db,
        transaction
    )

    return RedirectResponse(
        "/transactions-page",
        status_code=303
    )


# =========================================================
# HTML FORECAST
# =========================================================

@app.get(
    "/forecast-page",
    response_class=HTMLResponse
)
def forecast_page(
    request: Request,
    db: Session = Depends(get_db)
):
    forecast = get_financial_forecast(db)

    return templates.TemplateResponse(
        request=request,
        name="forecast.html",
        context={
            "forecast": forecast
        }
    )


# =========================================================
# HTML RECURRING
# =========================================================

@app.get(
    "/recurring-page",
    response_class=HTMLResponse
)
def recurring_page(
    request: Request,
    db: Session = Depends(get_db)
):
    recurring = crud.get_recurring_transactions(db)

    return templates.TemplateResponse(
        request=request,
        name="recurring.html",
        context={
            "recurring": recurring
        }
    )


# =========================================================
# INVENTORY
# =========================================================

@app.get(
    "/inventory",
    response_class=HTMLResponse
)
def inventory_page(
    request: Request,
    db: Session = Depends(get_db)
):
    inventory_items_db = db.query(
        models.InventoryItem
    ).all()

    inventory_items = []

    for item in inventory_items_db:

        product = db.query(
            models.Product
        ).filter(
            models.Product.id == item.product_id
        ).first()

        inventory_items.append({
            "id": item.id,
            "inventory_number": item.inventory_number,
            "product_id": item.product_id,
            "product_name": (
                product.name
                if product
                else "Неизвестный товар"
            ),
            "responsible_user_id": item.responsible_user_id,
            "room": item.room,
            "purchase_date": item.purchase_date,
            "purchase_price": item.purchase_price,
            "status": item.status,
            "qr_code": item.qr_code,
            "created_at": item.created_at
        })

    return templates.TemplateResponse(
        request=request,
        name="inventory.html",
        context={
            "inventory_items": inventory_items
        }
    )