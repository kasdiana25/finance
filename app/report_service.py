from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from . import models


# =========================================================
# ПЕРИОДЫ
# =========================================================

def get_period_dates(
    period: str,
    date_from=None,
    date_to=None
):
    today = date.today()

    if period == "today":

        return today, today

    elif period == "yesterday":

        yesterday = today - timedelta(days=1)

        return yesterday, yesterday

    elif period == "week":

        start = (
            today -
            timedelta(days=today.weekday())
        )

        return (
            start,
            start + timedelta(days=6)
        )

    elif period == "last_week":

        this_week_start = (
            today -
            timedelta(days=today.weekday())
        )

        return (
            this_week_start -
            timedelta(days=7),

            this_week_start -
            timedelta(days=1)
        )

    elif period == "month":

        start = today.replace(day=1)

        if today.month == 12:

            next_month = date(
                today.year + 1,
                1,
                1
            )

        else:

            next_month = date(
                today.year,
                today.month + 1,
                1
            )

        return (
            start,
            next_month - timedelta(days=1)
        )

    elif period == "last_month":

        first_this_month = \
            today.replace(day=1)

        last_month_end = \
            first_this_month - timedelta(days=1)

        return (
            last_month_end.replace(day=1),
            last_month_end
        )

    elif period == "year":

        return (
            date(today.year, 1, 1),
            date(today.year, 12, 31)
        )

    elif period == "custom":

        if not date_from or not date_to:

            raise ValueError(
                "Для произвольного периода "
                "необходимо указать даты"
            )

        return (
            date_from,
            date_to
        )

    return today, today


# =========================================================
# ПРЕОБРАЗОВАНИЕ ДАТЫ
# =========================================================

def _as_date(value):

    if isinstance(value, datetime):

        return value.date()

    if isinstance(value, date):

        return value

    if isinstance(value, str):

        try:

            return datetime.fromisoformat(
                value
            ).date()

        except ValueError:

            try:

                return datetime.strptime(
                    value,
                    "%Y-%m-%d"
                ).date()

            except ValueError:

                return None

    return value


# =========================================================
# ФИНАНСОВЫЙ ОТЧЁТ
# =========================================================

def get_financial_report(
    db: Session,
    period: str = "month",
    date_from=None,
    date_to=None
):

    start_date, end_date = \
        get_period_dates(
            period,
            date_from,
            date_to
        )


    # =====================================================
    # ОПЕРАЦИИ
    # =====================================================

    transactions = (
        db.query(
            models.Transaction
        ).all()
    )


    period_transactions = []


    # =====================================================
    # ОБЩИЕ СУММЫ
    # =====================================================

    income = 0.0
    expense = 0.0


    # =====================================================
    # ДОХОДЫ ПО ИСТОЧНИКАМ
    # =====================================================

    income_by_source = {

        "investment": 0.0,

        "show": 0.0

    }


    # =====================================================
    # ДОХОДЫ ПО СЧЕТАМ
    # =====================================================

    income_by_account = {

        "cash": 0.0,

        "bank_account": 0.0

    }


    # =====================================================
    # РАСХОДЫ ПО СЧЕТАМ
    # =====================================================

    expense_by_account = {

        "cash": 0.0,

        "bank_account": 0.0

    }


    # =====================================================
    # РАСХОДЫ ПО КАТЕГОРИЯМ
    # =====================================================

    expense_by_category = {}


    # =====================================================
    # ЗАРПЛАТА
    # =====================================================

    salary_total = 0.0

    salary_entries = []


    # =====================================================
    # ОБРАБОТКА ОПЕРАЦИЙ
    # =====================================================

    for transaction in transactions:

        transaction_date = _as_date(
            transaction.date
        )

        if not transaction_date:

            continue


        if not (
            start_date
            <= transaction_date
            <= end_date
        ):

            continue


        period_transactions.append(
            transaction
        )


        amount = float(
            transaction.amount or 0
        )


        # =================================================
        # ДОХОД
        # =================================================

        if transaction.type == "income":

            income += amount


            # ---------------------------------------------
            # Источник дохода
            # ---------------------------------------------

            if (
                transaction.income_source
                in income_by_source
            ):

                income_by_source[
                    transaction.income_source
                ] += amount


            # ---------------------------------------------
            # Счёт
            # ---------------------------------------------

            if (
                transaction.account
                in income_by_account
            ):

                income_by_account[
                    transaction.account
                ] += amount


        # =================================================
        # РАСХОД
        # =================================================

        elif transaction.type == "expense":

            expense += amount


            # ---------------------------------------------
            # Счёт
            # ---------------------------------------------

            if (
                transaction.account
                in expense_by_account
            ):

                expense_by_account[
                    transaction.account
                ] += amount


            # ---------------------------------------------
            # Категория
            # ---------------------------------------------

            category = (
                transaction.category
                or "Без категории"
            )


            expense_by_category[
                category
            ] = (
                expense_by_category.get(
                    category,
                    0.0
                )
                + amount
            )


            # ---------------------------------------------
            # ЗАРПЛАТА
            # ---------------------------------------------

            if category == "Заработная плата":

                salary_total += amount


                employee_name = (
                    transaction.paid_to
                )


                # Если paid_to пустой,
                # ищем сотрудника по employee_id

                if (
                    not employee_name
                    and transaction.employee_id
                ):

                    employee = (
                        db.query(
                            models.TelegramUser
                        )
                        .filter(
                            models.TelegramUser.id
                            ==
                            transaction.employee_id
                        )
                        .first()
                    )


                    if employee:

                        employee_name = (
                            employee.full_name
                        )


                salary_entries.append({

                    "transaction_id":
                        transaction.id,

                    "date":
                        transaction_date,

                    "employee_id":
                        transaction.employee_id,

                    "paid_to":
                        employee_name
                        or "Не указан",

                    "amount":
                        amount,

                    "account":
                        transaction.account,

                    "description":
                        transaction.description

                })


    # =========================================================
    # ТЕКУЩИЕ ОСТАТКИ ПО СЧЕТАМ
    #
    # Считаются по ВСЕМ операциям, а не только за выбранный
    # период.
    # =========================================================

    account_balances = {

        "cash": 0.0,

        "bank_account": 0.0

    }


    for transaction in transactions:

        account = (
            transaction.account
        )


        if account not in account_balances:

            continue


        amount = float(
            transaction.amount or 0
        )


        if transaction.type == "income":

            account_balances[
                account
            ] += amount


        elif transaction.type == "expense":

            account_balances[
                account
            ] -= amount


    # =========================================================
    # ВЫДАЧИ ДЕНЕГ
    # =========================================================

    transfers = (
        db.query(
            models.MoneyTransfer
        ).all()
    )

    period_transfers = []

    transfer_total = 0.0


    for transfer in transfers:

        transfer_date = _as_date(
            transfer.created_at
        )


        if not transfer_date:

            continue


        if (
            start_date
            <= transfer_date
            <= end_date
        ):

            period_transfers.append(
                transfer
            )

            transfer_total += float(
                transfer.amount or 0
            )


    # =========================================================
    # ЧЕКИ
    # =========================================================

    receipts = (
        db.query(
            models.Receipt
        ).all()
    )

    period_receipts = []


    for receipt in receipts:

        receipt_date = _as_date(
            receipt.receipt_date
            or receipt.uploaded_at
        )


        if (
            receipt_date
            and start_date
            <= receipt_date
            <= end_date
        ):

            period_receipts.append(
                receipt
            )


    # =========================================================
    # ЗАЯВКИ НА ПОКУПКУ
    # =========================================================

    purchase_requests = (
        db.query(
            models.PurchaseRequest
        ).all()
    )

    period_purchase_requests = []


    for request in purchase_requests:

        request_date = _as_date(
            request.created_at
        )


        if (
            request_date
            and start_date
            <= request_date
            <= end_date
        ):

            period_purchase_requests.append(
                request
            )


    # =========================================================
    # ЦЕЛЬ НА НЕДЕЛЮ
    #
    # Используем понедельник недели, к которой относится
    # начало выбранного периода.
    # =========================================================

    week_start = (
        start_date -
        timedelta(
            days=start_date.weekday()
        )
    )


    weekly_goal = (
        db.query(
            models.WeeklyGoal
        )
        .filter(
            models.WeeklyGoal.week_start
            ==
            week_start
        )
        .first()
    )


    if weekly_goal:

        goal_data = {

            "id":
                weekly_goal.id,

            "week_start":
                weekly_goal.week_start,

            # Основная цель
            "target_income":
                float(
                    weekly_goal.target_income
                    or 0
                ),

            # Фактический результат
            "result_income":
                round(
                    income,
                    2
                ),

            "description":
                weekly_goal.description

        }

    else:

        goal_data = None


    # =========================================================
    # БАЛАНС
    # =========================================================

    balance = (
        income - expense
    )


    # =========================================================
    # РЕЗУЛЬТАТ
    # =========================================================

    return {

        "period":
            period,

        "date_from":
            start_date,

        "date_to":
            end_date,


        # -----------------------------------------------
        # Основные показатели
        # -----------------------------------------------

        "income":
            round(
                income,
                2
            ),

        "expense":
            round(
                expense,
                2
            ),

        "balance":
            round(
                balance,
                2
            ),


        "transaction_count":
            len(
                period_transactions
            ),


        # -----------------------------------------------
        # Доходы по источникам
        # -----------------------------------------------

        "income_by_source": {

            "investment":
                round(
                    income_by_source[
                        "investment"
                    ],
                    2
                ),

            "show":
                round(
                    income_by_source[
                        "show"
                    ],
                    2
                )

        },


        # -----------------------------------------------
        # Доходы по счетам
        # -----------------------------------------------

        "income_by_account": {

            "cash":
                round(
                    income_by_account[
                        "cash"
                    ],
                    2
                ),

            "bank_account":
                round(
                    income_by_account[
                        "bank_account"
                    ],
                    2
                )

        },


        # -----------------------------------------------
        # Расходы по счетам
        # -----------------------------------------------

        "expense_by_account": {

            "cash":
                round(
                    expense_by_account[
                        "cash"
                    ],
                    2
                ),

            "bank_account":
                round(
                    expense_by_account[
                        "bank_account"
                    ],
                    2
                )

        },


        # -----------------------------------------------
        # Остатки
        # -----------------------------------------------

        "account_balances": {

            "cash":
                round(
                    account_balances[
                        "cash"
                    ],
                    2
                ),

            "bank_account":
                round(
                    account_balances[
                        "bank_account"
                    ],
                    2
                ),

            "total":
                round(
                    account_balances[
                        "cash"
                    ]
                    +
                    account_balances[
                        "bank_account"
                    ],
                    2
                )

        },


        # -----------------------------------------------
        # Категории расходов
        # -----------------------------------------------

        "expense_by_category": {

            key:
                round(
                    value,
                    2
                )

            for key, value
            in expense_by_category.items()

        },


        # -----------------------------------------------
        # Зарплата
        # -----------------------------------------------

        "salary_total":
            round(
                salary_total,
                2
            ),

        "salary_entries":
            salary_entries,


        # -----------------------------------------------
        # Цель недели
        # -----------------------------------------------

        "weekly_goal":
            goal_data,


        # -----------------------------------------------
        # Дополнительные данные
        # -----------------------------------------------

        "transfer_total":
            round(
                transfer_total,
                2
            ),

        "transfer_count":
            len(
                period_transfers
            ),

        "receipt_count":
            len(
                period_receipts
            ),

        "purchase_request_count":
            len(
                period_purchase_requests
            ),


        # -----------------------------------------------
        # Операции
        # -----------------------------------------------

        "transactions":
            period_transactions,

        "transfers":
            period_transfers,

        "receipts":
            period_receipts,

        "purchase_requests":
            period_purchase_requests

    }