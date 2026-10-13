import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from jinja2 import Environment, FileSystemLoader

from app.database import Base
from app import models, crud, schemas
from app.accounting import operation_rows
from app.report_service import get_financial_report
from app.statistics import get_statistics, INITIAL_BANK_BALANCE


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.today = date.today()
        self.employee = models.TelegramUser(telegram_id=123, full_name='Иван', role='employee')
        self.db.add(self.employee)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def spending(self, amount, account=None):
        t = models.Transaction(type='expense', category='Покупка', amount=amount, date=self.today, account=account)
        self.db.add(t)
        self.db.flush()
        self.db.add(models.EmployeeExpense(telegram_user_id=self.employee.telegram_id, transaction_id=t.id))
        return t

    def test_issued_money_is_debited_once_and_report_tracks_spending(self):
        transfer = crud.create_money_transfer(self.db, self.employee.id, 1000, 'Покупка', account='cash')
        spending = self.spending(400, account='cash')
        self.db.add(models.Receipt(transaction_id=spending.id, telegram_user_id=self.employee.id,
                                   transfer_id=transfer.id, filename='чек', filepath='', receipt_amount=400))
        self.db.commit()
        stats = get_statistics(self.db)
        self.assertEqual(stats['expense'], 1000)
        self.assertEqual(stats['balance'], INITIAL_BANK_BALANCE - 1000)
        self.assertEqual(crud.get_report_statistics(self.db)['total_expense'], 1000)
        report = get_financial_report(self.db, 'today')
        self.assertEqual(report['expense'], 1000)
        self.assertEqual(report['account_balances']['cash'], stats['bank']['balance'])
        self.assertEqual(report['employee_totals'][0]['issued'], 1000)
        self.assertEqual(report['employee_totals'][0]['spent'], 400)
        rows = operation_rows(self.db)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.paid_to == 'Иван' and row.account == 'cash' for row in rows))

    def test_cancelled_issuance_and_employee_spending_do_not_debit_bank(self):
        transfer = crud.create_money_transfer(self.db, self.employee.id, 1000, 'Покупка', account='bank_account')
        transfer.status = 'cancelled'
        self.spending(400)
        self.db.commit()
        self.assertEqual(get_statistics(self.db)['expense'], 0)
        report = get_financial_report(self.db, 'today')
        self.assertEqual(report['expense'], 0)
        self.assertEqual(report['employee_totals'][0]['issued'], 0)
        self.assertEqual(report['employee_totals'][0]['spent'], 400)

    def test_income_fields_are_saved_and_templates_display_them(self):
        income = crud.create_transaction(self.db, schemas.TransactionCreate(
            type='income', amount=2000, date=self.today, income_source='show', account='bank_account',
        ))
        self.assertEqual(income.income_source, 'show')
        self.assertEqual(income.account, 'bank_account')
        crud.create_money_transfer(self.db, self.employee.id, 1000, 'Покупка', account='bank_account')
        stats = get_statistics(self.db)
        self.assertEqual(stats['settlement']['balance'], 1000)
        env = Environment(loader=FileSystemLoader('app/templates'))
        from types import SimpleNamespace
        context = dict(transactions=operation_rows(self.db), employee_names={self.employee.id: 'Иван'},
                       statistics=stats, report=get_financial_report(self.db, 'today'),
                       request=SimpleNamespace(url=SimpleNamespace(path='/home')),
                       url_for=lambda *args, **kwargs: '/static/css/style.css')
        for template in ['index.html', 'transactions.html', 'reports.html']:
            with self.subTest(template=template):
                html = env.get_template(template).render(**context)
                for text in ['Показы', 'Расчётный счёт', 'Иван']:
                    self.assertIn(text, html)

    def test_report_includes_spending_of_money_issued_before_period(self):
        transfer = crud.create_money_transfer(self.db, self.employee.id, 1000, 'Покупка')
        transfer.created_at = self.today - timedelta(days=1)
        self.spending(400)
        self.db.commit()
        report = get_financial_report(self.db, 'today')
        self.assertEqual(report['expense'], 0)
        self.assertEqual(report['employee_totals'][0]['issued'], 0)
        self.assertEqual(report['employee_totals'][0]['spent'], 400)


if __name__ == '__main__':
    unittest.main()
