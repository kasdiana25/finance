"""Cash movements and employee spending are separate accounting records."""

from types import SimpleNamespace

from . import models


def employee_expense_ids(db):
    return {row[0] for row in db.query(models.EmployeeExpense.transaction_id).all()}


def operation_rows(db, transactions=None):
    """Build display rows without changing stored transactions or double debiting."""
    if transactions is None:
        transactions = db.query(models.Transaction).all()
    users = db.query(models.TelegramUser).all()
    employees = {e.id: e.full_name for e in users}
    telegram_ids = {e.telegram_id: e.id for e in users}
    expense_owners = {
        e.transaction_id: telegram_ids.get(e.telegram_user_id, e.telegram_user_id)
        for e in db.query(models.EmployeeExpense).all()
    }
    transfers = {t.id: t for t in db.query(models.MoneyTransfer).all()}
    receipt_transfers = {
        r.transaction_id: r.transfer_id
        for r in db.query(models.Receipt).all()
        if r.transaction_id and r.transfer_id
    }
    rows = []
    for t in transactions:
        values = {c.name: getattr(t, c.name) for c in models.Transaction.__table__.columns}
        owner = expense_owners.get(t.id)
        transfer = transfers.get(receipt_transfers.get(t.id))
        values['is_employee_expense'] = owner is not None
        if owner is not None:
            values['employee_id'] = owner
            values['account'] = t.account or (transfer.account if transfer else None)
        values['paid_to'] = employees.get(values['employee_id']) or t.paid_to
        rows.append(SimpleNamespace(**values))
    for t in transfers.values():
        if t.status == 'cancelled':
            continue
        rows.append(SimpleNamespace(
            id=t.id, type='expense', category='Выдача сотруднику',
            amount=t.amount, date=t.created_at, description=t.purpose,
            account=t.account, income_source=None, employee_id=t.telegram_user_id,
            paid_to=employees.get(t.telegram_user_id, 'Неизвестный сотрудник'),
            is_employee_expense=False,
        ))
    return sorted(rows, key=lambda row: (row.date, row.id), reverse=True)


def employee_period_totals(db, start_date, end_date):
    totals = {}
    users = db.query(models.TelegramUser).all()
    employees = {e.id: e.full_name for e in users}
    telegram_ids = {e.telegram_id: e.id for e in users}
    def entry(employee_id):
        return totals.setdefault(employee_id, {
            'employee_id': employee_id,
            'name': employees.get(employee_id, 'Неизвестный сотрудник'),
            'issued': 0.0, 'spent': 0.0,
        })
    for transfer in db.query(models.MoneyTransfer).all():
        if transfer.status != 'cancelled' and start_date <= transfer.created_at <= end_date:
            entry(transfer.telegram_user_id)['issued'] += float(transfer.amount)
    seen = set()
    for expense, transaction in db.query(models.EmployeeExpense, models.Transaction).join(
        models.Transaction, models.Transaction.id == models.EmployeeExpense.transaction_id
    ).all():
        if transaction.id not in seen and start_date <= transaction.date <= end_date:
            employee_id = telegram_ids.get(expense.telegram_user_id, expense.telegram_user_id)
            entry(employee_id)['spent'] += float(transaction.amount)
            seen.add(transaction.id)
    return list(totals.values())
