from sqlalchemy.orm import Session
from sqlalchemy import func

from .models import Transaction, MoneyTransfer


# =========================================================
# НАЧАЛЬНЫЙ ОСТАТОК
# =========================================================

INITIAL_BANK_BALANCE = 16600.0


def get_statistics(db: Session):

    # =====================================================
    # ОБЩИЕ ДОХОДЫ
    # =====================================================

    income = db.query(
        func.sum(Transaction.amount)
    ).filter(
        Transaction.type == "income"
    ).scalar() or 0


    # =====================================================
    # ОБЩИЕ РАСХОДЫ
    # =====================================================

    expense = db.query(
        func.sum(Transaction.amount)
    ).filter(
        Transaction.type == "expense"
    ).scalar() or 0


    # =====================================================
    # ДОХОДЫ — БАНК
    # cash = Банк
    # =====================================================

    bank_income = db.query(
        func.sum(Transaction.amount)
    ).filter(
        Transaction.type == "income",
        Transaction.account == "cash"
    ).scalar() or 0


    # =====================================================
    # ДОХОДЫ — РАСЧЁТНЫЙ СЧЁТ
    # =====================================================

    settlement_income = db.query(
        func.sum(Transaction.amount)
    ).filter(
        Transaction.type == "income",
        Transaction.account == "bank_account"
    ).scalar() or 0


    # =====================================================
    # РАСХОДЫ — БАНК
    # =====================================================

    bank_expense = db.query(
        func.sum(Transaction.amount)
    ).filter(
        Transaction.type == "expense",
        Transaction.account == "cash"
    ).scalar() or 0


    # =====================================================
    # РАСХОДЫ — РАСЧЁТНЫЙ СЧЁТ
    # =====================================================

    settlement_expense = db.query(
        func.sum(Transaction.amount)
    ).filter(
        Transaction.type == "expense",
        Transaction.account == "bank_account"
    ).scalar() or 0


    # =====================================================
    # ВЫДАНО СОТРУДНИКАМ — БАНК
    # =====================================================

    bank_transfers = db.query(
        func.sum(MoneyTransfer.amount)
    ).filter(
        MoneyTransfer.account == "cash",
        MoneyTransfer.status != "cancelled"
    ).scalar() or 0


    # =====================================================
    # ВЫДАНО СОТРУДНИКАМ — РАСЧЁТНЫЙ СЧЁТ
    # =====================================================

    settlement_transfers = db.query(
        func.sum(MoneyTransfer.amount)
    ).filter(
        MoneyTransfer.account == "bank_account",
        MoneyTransfer.status != "cancelled"
    ).scalar() or 0


    # =====================================================
    # БАЛАНС БАНКА
    # =====================================================

    bank_balance = (
        INITIAL_BANK_BALANCE
        + bank_income
        - bank_expense
        - bank_transfers
    )


    # =====================================================
    # БАЛАНС РАСЧЁТНОГО СЧЁТА
    # =====================================================

    settlement_balance = (
        settlement_income
        - settlement_expense
        - settlement_transfers
    )


    # =====================================================
    # ОБЩИЙ БАЛАНС
    # =====================================================

    balance = (
        bank_balance
        + settlement_balance
    )


    # =====================================================
    # ФАКТИЧЕСКИЕ РАСХОДЫ
    #
    # Выдача сотруднику тоже является расходом денег.
    # =====================================================

    total_expense = (
        expense
        + bank_transfers
        + settlement_transfers
    )


    return {

        # Общие показатели
        "income": income,
        "expense": total_expense,
        "balance": balance,

        # =================================================
        # БАНК
        # =================================================

        "bank": {
            "initial_balance": INITIAL_BANK_BALANCE,
            "income": bank_income,
            "expense": bank_expense,
            "transfers": bank_transfers,
            "balance": bank_balance
        },

        # =================================================
        # РАСЧЁТНЫЙ СЧЁТ
        # =================================================

        "settlement": {
            "initial_balance": 0,
            "income": settlement_income,
            "expense": settlement_expense,
            "transfers": settlement_transfers,
            "balance": settlement_balance
        }
    }