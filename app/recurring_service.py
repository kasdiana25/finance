from datetime import date
from dateutil.relativedelta import relativedelta

from . import models


def generate_recurring_transactions(db):

    today = date.today()


    payments = db.query(
        models.RecurringTransaction
    ).filter(
        models.RecurringTransaction.is_active == True
    ).all()


    created = []


    for payment in payments:


        if payment.next_payment_date <= today:


            transaction = models.Transaction(

                type=payment.type,

                category=payment.category,

                amount=payment.amount,

                description=payment.name,

                date=payment.next_payment_date,

                recurring_id=payment.id
            )


            db.add(transaction)


            if payment.frequency == "monthly":

                payment.next_payment_date += relativedelta(
                    months=1
                )


            elif payment.frequency == "yearly":

                payment.next_payment_date += relativedelta(
                    years=1
                )


            elif payment.frequency == "weekly":

                payment.next_payment_date += relativedelta(
                    weeks=1
                )


            created.append(transaction)



    db.commit()


    return created