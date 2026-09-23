from datetime import date

from . import models



def generate_notifications(db):

    notifications = []


    today = date.today()


    payments = db.query(
        models.RecurringTransaction
    ).filter(
        models.RecurringTransaction.is_active == True
    ).all()



    for payment in payments:


        days_left = (
            payment.next_payment_date - today
        ).days



        if 0 <= days_left <= 30:


            exists = db.query(
                models.Notification
            ).filter(
                models.Notification.message ==
                f"{payment.name} - {payment.amount} ₽ через {days_left} дней"
            ).first()



            if exists:
                continue



            notification = models.Notification(

                title="Ближайший платёж",

                message=
                f"{payment.name} - {payment.amount} ₽ через {days_left} дней",

                type="warning",

                is_read=False,

                created_at=today
            )


            db.add(notification)

            notifications.append(notification)



    db.commit()



    for item in notifications:
        db.refresh(item)



    return notifications