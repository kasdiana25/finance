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

from sqlalchemy.orm import Session
from pathlib import Path

from .database import Base, engine, get_db
from . import schemas, crud

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
# ROOT
# =========================================================

@app.get("/")
def root():

    return {
        "message": "Finance Max Alice работает!"
    }



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

@app.get(
    "/calendar",
    response_class=HTMLResponse
)
def calendar_page(
    request: Request,
    db: Session = Depends(get_db)
):

    payments = crud.get_future_transactions(db)


    import calendar
    from datetime import date


    today = date.today()


    month_name = (
        calendar.month_name[today.month]
    )


    days = []


    days_count = calendar.monthrange(
        today.year,
        today.month
    )[1]



    for number in range(1, days_count + 1):


        day_payments = []


        for payment in payments:


            if payment.next_payment_date.day == number:

                day_payments.append(
                    payment
                )



        days.append({

            "number": number,

            "payments": day_payments

        })



    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={

            "days": days,

            "month":
            f"{month_name} {today.year}"

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
# API NOTIFICATIONS
# =========================================================

@app.get(
    "/api/notifications",
    response_model=List[schemas.NotificationResponse]
)
def notifications_api(
    db: Session = Depends(get_db)
):

    generate_notifications(db)

    return crud.get_notifications(db)



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
# HTML ADD TRANSACTION
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
# HTML CALENDAR
# =========================================================

@app.get(
    "/calendar",
    response_class=HTMLResponse
)
def calendar_page(
    request: Request,
    db: Session = Depends(get_db)
):

    payments = crud.get_future_transactions(db)


    return templates.TemplateResponse(
        request=request,
        name="calendar.html",
        context={

            "payments": payments

        }
    )



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