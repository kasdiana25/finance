from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from . import models


def get_period_dates(period: str, date_from=None, date_to=None):
    """
    Возвращает начальную и конечную дату выбранного периода.
    """

    today = date.today()

    if period == "today":
        return today, today

    elif period == "yesterday":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday

    elif period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start, end

    elif period == "last_week":
        this_week_start = today - timedelta(days=today.weekday())
        start = this_week_start - timedelta(days=7)
        end = this_week_start - timedelta(days=1)

        return start, end

    elif period == "month":
        start = today.replace(day=1)

        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)

        end = next_month - timedelta(days=1)

        return start, end

    elif period == "last_month":

        first_this_month = today.replace(day=1)

        last_month_end = first_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)

        return last_month_start, last_month_end

    elif period == "year":

        start = date(today.year, 1, 1)
        end = date(today.year, 12, 31)

        return start, end

    elif period == "custom":

        if not date_from or not date_to:
            raise ValueError(
                "Для произвольного периода необходимо указать даты"
            )

        return date_from, date_to

    else:
        return today, today


def get_financial_report(
    db: Session,
    period: str = "month",
    date_from=None,
    date_to=None
):

    start_date, end_date = get_period_dates(
        period,
        date_from,
        date_to
    )

    # -----------------------------------------------------
    # ТРАНЗАКЦИИ
    # -----------------------------------------------------

    transactions = (
        db.query(models.Transaction)
        .all()
    )

    period_transactions = []

    income = 0
    expense = 0

    for transaction in transactions:

        transaction_date = transaction.date

        # Если дата хранится строкой
        if isinstance(transaction_date, str):

            try:
                transaction_date = datetime.fromisoformat(
                    transaction_date
                ).date()

            except ValueError:

                try:
                    transaction_date = datetime.strptime(
                        transaction_date,
                        "%Y-%m-%d"
                    ).date()

                except ValueError:
                    continue

        # Если datetime
        elif isinstance(transaction_date, datetime):

            transaction_date = transaction_date.date()

        if not transaction_date:
            continue

        if start_date <= transaction_date <= end_date:

            period_transactions.append(transaction)

            if transaction.type == "income":
                income += float(transaction.amount or 0)

            elif transaction.type == "expense":
                expense += float(transaction.amount or 0)

    # -----------------------------------------------------
    # ВЫДАЧИ ДЕНЕГ
    # -----------------------------------------------------

    transfers = (
        db.query(models.MoneyTransfer)
        .all()
    )

    period_transfers = []

    transfer_total = 0

    for transfer in transfers:

        created_at = transfer.created_at

        if not created_at:
            continue

        if isinstance(created_at, datetime):
            transfer_date = created_at.date()
        else:
            transfer_date = created_at

        if start_date <= transfer_date <= end_date:

            period_transfers.append(transfer)

            transfer_total += float(
                transfer.amount or 0
            )

    # -----------------------------------------------------
    # ЧЕКИ
    # -----------------------------------------------------

    receipts = (
        db.query(models.Receipt)
        .all()
    )

    period_receipts = []

    for receipt in receipts:

        created_at = receipt.created_at

        if not created_at:
            continue

        if isinstance(created_at, datetime):
            receipt_date = created_at.date()
        else:
            receipt_date = created_at

        if start_date <= receipt_date <= end_date:
            period_receipts.append(receipt)

    # -----------------------------------------------------
    # ЗАЯВКИ НА ЗАКУПКУ
    # -----------------------------------------------------

    purchase_requests = (
        db.query(models.PurchaseRequest)
        .all()
    )

    period_purchase_requests = []

    purchase_total = 0

    for request in purchase_requests:

        created_at = request.created_at

        if not created_at:
            continue

        if isinstance(created_at, datetime):
            request_date = created_at.date()
        else:
            request_date = created_at

        if start_date <= request_date <= end_date:

            period_purchase_requests.append(request)

    # -----------------------------------------------------
    # ИТОГ
    # -----------------------------------------------------

    balance = income - expense

    return {
        "period": period,

        "date_from": start_date,
        "date_to": end_date,

        "income": round(income, 2),
        "expense": round(expense, 2),
        "balance": round(balance, 2),

        "transaction_count": len(
            period_transactions
        ),

        "transfer_total": round(
            transfer_total,
            2
        ),

        "transfer_count": len(
            period_transfers
        ),

        "receipt_count": len(
            period_receipts
        ),

        "purchase_request_count": len(
            period_purchase_requests
        ),

        "transactions": period_transactions,

        "transfers": period_transfers,

        "receipts": period_receipts,

        "purchase_requests": period_purchase_requests
    }