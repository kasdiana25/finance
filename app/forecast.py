from datetime import date

from sqlalchemy.orm import Session

from . import models



def get_financial_forecast(db: Session):

    # все операции
    transactions = db.query(
        models.Transaction
    ).all()


    income = 0
    expense = 0


    for transaction in transactions:

        if transaction.type == "income":

            income += transaction.amount


        elif transaction.type == "expense":

            expense += transaction.amount



    current_balance = income - expense



    # будущие платежи

    future_payments = db.query(
        models.RecurringTransaction
    ).filter(
        models.RecurringTransaction.is_active == True
    ).all()



    planned_expenses = 0


    payments = []


    for payment in future_payments:

        if payment.type == "expense":

            planned_expenses += payment.amount


            payments.append(
                {
                    "name": payment.name,
                    "amount": payment.amount,
                    "date": payment.next_payment_date
                }
            )



    predicted_balance = (
        current_balance - planned_expenses
    )



    return {

        "income": income,

        "expense": expense,

        "current_balance": current_balance,

        "planned_expenses": planned_expenses,

        "predicted_balance": predicted_balance,

        "future_payments": payments
    }