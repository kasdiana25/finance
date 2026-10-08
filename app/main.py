import io
from pathlib import Path
import os

from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
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
    RedirectResponse,
    FileResponse
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
from .report_service import get_financial_report


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
# ОТКРЫТЬ ФАЙЛ ЧЕКА
# =========================================================

@app.get("/receipts/file/{receipt_id}")
def open_receipt_file(
    receipt_id: int,
    db: Session = Depends(get_db)
):
    receipt = (
        db.query(models.Receipt)
        .filter(models.Receipt.id == receipt_id)
        .first()
    )

    if not receipt:
        raise HTTPException(
            status_code=404,
            detail="Чек не найден"
        )

    path = (receipt.filepath or "").strip()

    if not path or path.lower() in ("pending", "ожидается", "none", "null", ""):
        raise HTTPException(
            status_code=404,
            detail="Файл чека ещё не загружен"
        )

    file_path = Path(path)

    # Если путь относительный — ищем файл в разных местах
    if not file_path.is_absolute():
        candidates = [
            BASE_DIR / path,
            BASE_DIR / "uploads" / path,
            BASE_DIR / "static" / "receipts" / path,
            Path.cwd() / path,
            Path.cwd() / "uploads" / path,
        ]
        for candidate in candidates:
            if candidate.is_file():
                file_path = candidate
                break

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Файл не найден на диске. Путь в БД: {path}"
        )

    # Правильный тип файла, чтобы браузер мог открыть
    suffix = file_path.suffix.lower()
    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".pdf": "application/pdf",
        ".webp": "image/webp",
    }
    media_type = media_types.get(suffix, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        filename=receipt.filename or file_path.name,
        media_type=media_type,
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
# WEEKLY GOALS
# =========================================================

@app.get(
    "/api/weekly-goal",
    response_model=schemas.WeeklyGoalResponse
)
def get_weekly_goal_api(
    week_start: str,
    db: Session = Depends(get_db)
):
    from datetime import date as date_type

    goal = crud.get_weekly_goal(
        db,
        date_type.fromisoformat(week_start)
    )

    if goal is None:
        raise HTTPException(
            status_code=404,
            detail="Цель на эту неделю не задана"
        )

    return goal


@app.post(
    "/api/weekly-goal",
    response_model=schemas.WeeklyGoalResponse
)
def save_weekly_goal_api(
    goal: schemas.WeeklyGoalCreate,
    db: Session = Depends(get_db)
):
    return crud.create_or_update_weekly_goal(
        db,
        goal
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
    period: str = "month",
    date_from: str = None,
    date_to: str = None,
    db: Session = Depends(get_db)
):

    from datetime import date

    custom_from = None
    custom_to = None

    if period == "custom":

        try:
            if date_from:
                custom_from = date.fromisoformat(
                    date_from
                )

            if date_to:
                custom_to = date.fromisoformat(
                    date_to
                )

        except ValueError:

            custom_from = None
            custom_to = None

    report = get_financial_report(
        db=db,
        period=period,
        date_from=custom_from,
        date_to=custom_to
    )

    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "report": report
        }
    )



def register_pdf_fonts():
    regular = Path(
        os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\Windows\Fonts\DejaVuSans.ttf"
        )
    )

    bold = Path(
        os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\Windows\Fonts\DejaVuSans-Bold.ttf"
        )
    )

    if not regular.exists():
        raise FileNotFoundError(
            f"Не найден шрифт: {regular}"
        )

    if not bold.exists():
        raise FileNotFoundError(
            f"Не найден шрифт: {bold}"
        )

    pdfmetrics.registerFont(
        TTFont("DejaVuSans", str(regular))
    )

    pdfmetrics.registerFont(
        TTFont("DejaVuSans-Bold", str(bold))
    )


@app.get("/reports/pdf")
def reports_pdf(
    period: str = "month",
    date_from: str = None,
    date_to: str = None,
    db: Session = Depends(get_db)
):
    from datetime import date
    from io import BytesIO
    from collections import defaultdict

    from fastapi.responses import StreamingResponse
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    # ---------------------------------------------------------
    # Период
    # ---------------------------------------------------------
    custom_from = None
    custom_to = None

    if period == "custom":
        try:
            if date_from:
                custom_from = date.fromisoformat(date_from)

            if date_to:
                custom_to = date.fromisoformat(date_to)
        except ValueError:
            custom_from = None
            custom_to = None

    report = get_financial_report(
        db=db,
        period=period,
        date_from=custom_from,
        date_to=custom_to
    )

    # ---------------------------------------------------------
    # Вспомогательные функции
    # ---------------------------------------------------------
    def safe_text(value):
        if value is None:
            return ""

        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def money(value):
        try:
            value = float(value or 0)
        except (TypeError, ValueError):
            value = 0

        return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"

    def num(value):
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def get_value(obj, key, default=0):
        if isinstance(obj, dict):
            return obj.get(key, default)

        return getattr(obj, key, default)

    # ---------------------------------------------------------
    # Данные отчёта
    # ---------------------------------------------------------
    income = num(report.get("income", 0))
    expense = num(report.get("expense", 0))
    balance = num(report.get("balance", income - expense))

    income_by_source = report.get("income_by_source", {}) or {}

    investment = num(
        income_by_source.get("investment", 0)
    )

    show = num(
        income_by_source.get("show", 0)
    )

    income_by_account = report.get("income_by_account", {}) or {}
    expense_by_account = report.get("expense_by_account", {}) or {}

    cash_income = num(
        income_by_account.get("cash", 0)
    )

    bank_income = num(
        income_by_account.get("bank_account", 0)
    )

    cash_expense = num(
        expense_by_account.get("cash", 0)
    )

    bank_expense = num(
        expense_by_account.get("bank_account", 0)
    )

    # ---------------------------------------------------------
    # Балансы счетов
    # ---------------------------------------------------------
    account_balances = report.get("account_balances", {}) or {}

    cash_balance = num(
        account_balances.get(
            "cash",
            cash_income - cash_expense
        )
    )

    bank_balance = num(
        account_balances.get(
            "bank_account",
            bank_income - bank_expense
        )
    )

    # ---------------------------------------------------------
    # Зарплата
    # ---------------------------------------------------------
    salary_entries = report.get("salary_entries", []) or []

    salary_total = num(
        report.get("salary_total", 0)
    )

    # ---------------------------------------------------------
    # Обычные выданные деньги
    #
    # ВАЖНО:
    # сюда НЕ попадает категория "Заработная плата"
    # ---------------------------------------------------------
    issued_by_person = defaultdict(float)

    # ---------------------------------------------------------
    # Расходы по категориям
    # ---------------------------------------------------------
    expense_by_category = report.get(
        "expense_by_category",
        {}
    ) or {}

    # ---------------------------------------------------------
    # Количество операций
    # ---------------------------------------------------------
    transactions = report.get("transactions", []) or []

    total_operations = len(transactions)

    income_operations = 0
    expense_operations = 0

    for transaction in transactions:
        transaction_type = get_value(
            transaction,
            "type",
            ""
        )

        if transaction_type == "income":
            income_operations += 1

        elif transaction_type == "expense":
            expense_operations += 1

        # Выданные деньги без зарплаты
        if transaction_type == "expense":
            category = get_value(
                transaction,
                "category",
                ""
            )

            if category != "Заработная плата":
                paid_to = get_value(
                    transaction,
                    "paid_to",
                    ""
                )

                amount = num(
                    get_value(
                        transaction,
                        "amount",
                        0
                    )
                )

                if paid_to:
                    issued_by_person[
                        str(paid_to).strip()
                    ] += amount

    # ---------------------------------------------------------
    # Если transactions отсутствует или пуст,
    # пробуем использовать salary_entries только для зарплаты.
    # ---------------------------------------------------------
    if not salary_entries:
        salary_entries = []

    # ---------------------------------------------------------
    # Цель недели
    # ---------------------------------------------------------
    weekly_goal = report.get(
        "weekly_goal"
    )

    goal_value = 0
    goal_result = income

    if weekly_goal:
        goal_value = num(
            get_value(
                weekly_goal,
                "target_income",
                0
            )
        )

        goal_result = num(
            get_value(
                weekly_goal,
                "result_income",
                income
            )
        )

    # ---------------------------------------------------------
    # Даты отчёта
    # ---------------------------------------------------------
    start_date = report.get("start_date")
    end_date = report.get("end_date")

    if start_date and end_date:
        period_text = (
            f"{safe_text(start_date)} — "
            f"{safe_text(end_date)}"
        )
    elif custom_from and custom_to:
        period_text = (
            f"{custom_from.strftime('%d.%m.%Y')} — "
            f"{custom_to.strftime('%d.%m.%Y')}"
        )
    else:
        period_text = "Выбранный период"

    # ---------------------------------------------------------
    # Шрифты
    # ---------------------------------------------------------
    register_pdf_fonts()

    normal_font = "DejaVuSans"
    bold_font = "DejaVuSans-Bold"

    # ---------------------------------------------------------
    # PDF
    # ---------------------------------------------------------
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=10 * mm,
        title="Финансовый отчёт",
        author="Finance"
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "PDFTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=17,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=3 * mm,
    )

    period_style = ParagraphStyle(
        "PDFPeriod",
        parent=styles["Normal"],
        fontName=normal_font,
        fontSize=8.5,
        leading=10,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#555555"),
        spaceAfter=5 * mm,
    )

    section_style = ParagraphStyle(
        "PDFSection",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=10.5,
        leading=12,
        textColor=colors.HexColor("#222222"),
        spaceBefore=3 * mm,
        spaceAfter=2 * mm,
    )

    cell_style = ParagraphStyle(
        "PDFCell",
        parent=styles["Normal"],
        fontName=normal_font,
        fontSize=7.7,
        leading=9,
    )

    cell_bold_style = ParagraphStyle(
        "PDFCellBold",
        parent=cell_style,
        fontName=bold_font,
    )

    right_cell_style = ParagraphStyle(
        "PDFRight",
        parent=cell_style,
        alignment=TA_RIGHT,
    )

    right_bold_style = ParagraphStyle(
        "PDFRightBold",
        parent=right_cell_style,
        fontName=bold_font,
    )

    # ---------------------------------------------------------
    # Стили таблиц
    # ---------------------------------------------------------
    header_bg = colors.HexColor("#E9EEF5")
    total_bg = colors.HexColor("#F1F3F5")
    border_color = colors.HexColor("#C8CDD3")

    def P(text, bold=False, right=False):
        if bold and right:
            style = right_bold_style
        elif bold:
            style = cell_bold_style
        elif right:
            style = right_cell_style
        else:
            style = cell_style

        return Paragraph(
            safe_text(text),
            style
        )

    def make_table(
        data,
        widths,
        header=True,
        total_rows=None
    ):
        table = Table(
            data,
            colWidths=widths,
            repeatRows=1 if header else 0,
            hAlign="LEFT"
        )

        commands = [
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.4,
                border_color
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                4
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                4
            ),
        ]

        if header:
            commands.extend([
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    header_bg
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    bold_font
                ),
            ])

        if total_rows:
            for row_index in total_rows:
                commands.extend([
                    (
                        "BACKGROUND",
                        (0, row_index),
                        (-1, row_index),
                        total_bg
                    ),
                    (
                        "FONTNAME",
                        (0, row_index),
                        (-1, row_index),
                        bold_font
                    ),
                ])

        table.setStyle(
            TableStyle(commands)
        )

        return table

    story = []

    # =========================================================
    # ЗАГОЛОВОК
    # =========================================================
    story.append(
        Paragraph(
            "ФИНАНСОВЫЙ ОТЧЁТ",
            title_style
        )
    )

    story.append(
        Paragraph(
            f"Период: {period_text}",
            period_style
        )
    )

    # =========================================================
    # 1. ФИНАНСОВЫЙ ИТОГ
    # =========================================================
    story.append(
        Paragraph(
            "1. Финансовый итог",
            section_style
        )
    )

    financial_table = [
        [
            P("Показатель", True),
            P("Сумма", True, True)
        ],
        [
            P("Доход"),
            P(money(income), False, True)
        ],
        [
            P("Расход"),
            P(money(expense), False, True)
        ],
        [
            P("Остаток", True),
            P(money(balance), True, True)
        ],
    ]

    story.append(
        make_table(
            financial_table,
            [120 * mm, 55 * mm],
            total_rows=[3]
        )
    )

        # =========================================================
    # 2. ДОХОДЫ И ДЕНЬГИ ПО СЧЕТАМ
    # =========================================================
    story.append(
        Paragraph(
            "2. Доходы и деньги по счетам",
            section_style
        )
    )

    accounts_table = [
        [
            P("Показатель", True),
            P("Доход", True, True),
            P("Расход", True, True),
            P("Остаток", True, True),
        ],

        [
            P("Инвестиции"),
            P(money(investment), False, True),
            P("—", False, True),
            P("—", False, True),
        ],

        [
            P("Показы"),
            P(money(show), False, True),
            P("—", False, True),
            P("—", False, True),
        ],

        [
            P("Наличные"),
            P(money(cash_income), False, True),
            P(money(cash_expense), False, True),
            P(money(cash_balance), True, True),
        ],

        [
            P("Расчётный счёт"),
            P(money(bank_income), False, True),
            P(money(bank_expense), False, True),
            P(money(bank_balance), True, True),
        ],
    ]

    # Более компактная таблица, чтобы гарантированно
    # помещалась в ширину A4
    accounts_table_obj = Table(
        accounts_table,
        colWidths=[
            58 * mm,
            40 * mm,
            40 * mm,
            40 * mm,
        ],
        hAlign="LEFT"
    )

    accounts_table_obj.setStyle(
        TableStyle([
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.4,
                border_color
            ),
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                header_bg
            ),
            (
                "FONTNAME",
                (0, 0),
                (-1, 0),
                bold_font
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "LEFTPADDING",
                (0, 0),
                (-1, -1),
                3
            ),
            (
                "RIGHTPADDING",
                (0, 0),
                (-1, -1),
                3
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                3
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                3
            ),
        ])
    )

    story.append(accounts_table_obj) 
    # =========================================================
    # 3. ВЫДАННЫЕ ДЕНЬГИ БЕЗ ЗАРПЛАТЫ
    # =========================================================
    story.append(
        Paragraph(
            "3. Выданные деньги без зарплаты",
            section_style
        )
    )

    issued_table = [
        [
            P("Кому выдано", True),
            P("Выдано", True, True),
            P("Потрачено без ЗП", True, True),
        ]
    ]

    total_issued = 0
    total_spent_without_salary = 0

    # ---------------------------------------------------------
    # Здесь считаем:
    # "Выдано" = сумма операций этому получателю
    # "Потрачено без ЗП" = сумма обычных расходов.
    #
    # Если у получателя нет отдельного поля "выдано/потрачено",
    # обе суммы будут одинаковыми, потому что текущая модель
    # хранит саму операцию расхода.
    # ---------------------------------------------------------
    for person, amount in sorted(
        issued_by_person.items(),
        key=lambda x: x[0].lower()
    ):
        amount = num(amount)

        total_issued += amount
        total_spent_without_salary += amount

        issued_table.append([
            P(person),
            P(money(amount), False, True),
            P(money(amount), False, True),
        ])

    if not issued_by_person:
        issued_table.append([
            P("Нет данных"),
            P(money(0), False, True),
            P(money(0), False, True),
        ])

    issued_table.append([
        P("Итого", True),
        P(money(total_issued), True, True),
        P(money(total_spent_without_salary), True, True),
    ])

    story.append(
        make_table(
            issued_table,
            [85 * mm, 40 * mm, 55 * mm],
            total_rows=[len(issued_table) - 1]
        )
    )

    # =========================================================
    # 4. ЗАРПЛАТА
    # =========================================================
    story.append(
        Paragraph(
            "4. Зарплата",
            section_style
        )
    )

    salary_table = [
        [
            P("Кому выплачено", True),
            P("Сумма", True, True)
        ]
    ]

    calculated_salary_total = 0

    for entry in salary_entries:
        paid_to = get_value(
            entry,
            "paid_to",
            ""
        )

        amount = num(
            get_value(
                entry,
                "amount",
                0
            )
        )

        if not paid_to:
            paid_to = "Получатель не указан"

        calculated_salary_total += amount

        salary_table.append([
            P(paid_to),
            P(money(amount), False, True)
        ])

    # Если salary_total есть в отчёте,
    # используем его как итог.
    if salary_total:
        final_salary_total = salary_total
    else:
        final_salary_total = calculated_salary_total

    if not salary_entries:
        salary_table.append([
            P("Нет выплат"),
            P(money(0), False, True)
        ])

    salary_table.append([
        P("Всего потрачено на ЗП", True),
        P(money(final_salary_total), True, True)
    ])

    story.append(
        make_table(
            salary_table,
            [120 * mm, 60 * mm],
            total_rows=[len(salary_table) - 1]
        )
    )

    # =========================================================
    # 5. РАСХОДЫ ПО КАТЕГОРИЯМ
    # =========================================================
    story.append(
        Paragraph(
            "5. Расходы по категориям",
            section_style
        )
    )

    category_table = [
        [
            P("Категория", True),
            P("Сумма", True, True)
        ]
    ]

    category_total = 0

    for category, amount in sorted(
        expense_by_category.items(),
        key=lambda x: num(x[1]),
        reverse=True
    ):
        amount = num(amount)
        category_total += amount

        category_table.append([
            P(category),
            P(money(amount), False, True)
        ])

    if not expense_by_category:
        category_table.append([
            P("Нет расходов"),
            P(money(0), False, True)
        ])

    category_table.append([
        P("Всего расходов", True),
        P(money(category_total), True, True)
    ])

    story.append(
        make_table(
            category_table,
            [120 * mm, 60 * mm],
            total_rows=[len(category_table) - 1]
        )
    )

    # =========================================================
    # 6. ОПЕРАЦИИ ЗА ПЕРИОД
    # =========================================================
    story.append(
        Paragraph(
            "6. Операции за период",
            section_style
        )
    )

    operations_table = [
        [
            P("Показатель", True),
            P("Количество", True, True)
        ],
        [
            P("Всего операций"),
            P(str(total_operations), False, True)
        ],
        [
            P("Доходных операций"),
            P(str(income_operations), False, True)
        ],
        [
            P("Расходных операций"),
            P(str(expense_operations), False, True)
        ],
    ]

    story.append(
        make_table(
            operations_table,
            [120 * mm, 60 * mm]
        )
    )

    # =========================================================
    # 7. ЦЕЛЬ НЕДЕЛИ
    # =========================================================
    if goal_value > 0:
        story.append(
            Paragraph(
                "7. Цель недели",
                section_style
            )
        )

        goal_table = [
            [
                P("Показатель", True),
                P("Сумма", True, True)
            ],
            [
                P("Цель"),
                P(money(goal_value), False, True)
            ],
            [
                P("Результат"),
                P(money(goal_result), True, True)
            ],
        ]

        story.append(
            make_table(
                goal_table,
                [120 * mm, 60 * mm],
                total_rows=[2]
            )
        )

    # =========================================================
    # Сборка PDF
    # =========================================================
    doc.build(story)

    buffer.seek(0)

    filename = "financial_report.pdf"

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            )
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
    request: Request,
    db: Session = Depends(get_db)
):
    employees = crud.get_employees(db)

    return templates.TemplateResponse(
        request=request,
        name="add_transaction.html",
        context={
            "employees": employees
        }
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
    income_source: str = Form(None),
    account: str = Form(None),
    employee_id: int = Form(None),
    paid_to: str = Form(None),
    db: Session = Depends(get_db)
):
    from datetime import date as date_type

    transaction = schemas.TransactionCreate(
        type=type,
        category=category,
        amount=amount,
        date=date_type.fromisoformat(date),
        description=description or None,
        income_source=income_source or None,
        account=account or None,
        employee_id=employee_id,
        paid_to=paid_to or None
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