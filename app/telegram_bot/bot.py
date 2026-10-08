import os
import re
import uuid
import datetime

from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest
from ..database import SessionLocal

from ..models import (
    Transaction,
    TelegramUser,
    EmployeeExpense,
    Receipt,
    MoneyTransfer,
    PurchaseRequest,
    PurchaseRequestItem,
)

from .roles import get_user, create_user
from .receipt_ocr import recognize_receipt


# =========================================================
# НАСТРОЙКИ
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DIRECTOR_TELEGRAM_ID = os.getenv("DIRECTOR_TELEGRAM_ID")
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY")

if not BOT_TOKEN:
    raise RuntimeError(
        "Не найден TELEGRAM_BOT_TOKEN в .env"
    )

if DIRECTOR_TELEGRAM_ID:
    try:
        DIRECTOR_TELEGRAM_ID = int(
            DIRECTOR_TELEGRAM_ID
        )
    except ValueError:
        raise RuntimeError(
            "DIRECTOR_TELEGRAM_ID должен содержать число"
        )
else:
    DIRECTOR_TELEGRAM_ID = None


# =========================================================
# ПАПКА ДЛЯ ЧЕКОВ
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

RECEIPTS_DIR = BASE_DIR / "receipts"

RECEIPTS_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# БАЗА ДАННЫХ
# =========================================================

def get_db():
    return SessionLocal()


# =========================================================
# УВЕДОМЛЕНИЯ
# =========================================================

async def send_subscription_notification(
    bot,
    title: str,
    message: str
):
    if not DIRECTOR_TELEGRAM_ID:
        print(
            "⚠️ DIRECTOR_TELEGRAM_ID не задан."
        )
        return

    text = (
        f"🔔 {title}\n\n"
        f"{message}"
    )

    try:
        await bot.send_message(
            chat_id=DIRECTOR_TELEGRAM_ID,
            text=text
        )

        print(
            "✅ Telegram-уведомление отправлено директору."
        )

    except Exception as error:
        print(
            "❌ Ошибка отправки уведомления:",
            error
        )


# =========================================================
# ПОЛУЧЕНИЕ АКТИВНОЙ ВЫДАЧИ
# =========================================================

def get_active_money_transfer(
    db,
    telegram_id: int
):
    """
    Находит последнюю активную выдачу денег
    сотруднику, у которой ещё осталась
    неизрасходованная сумма.
    """

    user = (
        db.query(TelegramUser)
        .filter(
            TelegramUser.telegram_id == telegram_id
        )
        .first()
    )

    if not user:
        return None

    transfers = (
        db.query(MoneyTransfer)
        .filter(
            MoneyTransfer.telegram_user_id == user.id,
            MoneyTransfer.status.in_(
                ["issued", "partially_spent"]
            )
        )
        .order_by(
            MoneyTransfer.id.desc()
        )
        .all()
    )

    for transfer in transfers:

        spent = get_transfer_spent_amount(
            db,
            transfer.id
        )

        if spent < float(transfer.amount):
            return transfer

    return None


# =========================================================
# СТАТУС ВЫДАЧИ
# =========================================================

def update_transfer_status(
    db,
    transfer
):
    """
    Обновляет статус выдачи:
    issued
    partially_spent
    completed
    """

    spent_amount = get_transfer_spent_amount(
        db,
        transfer.id
    )

    transfer_amount = float(
        transfer.amount
    )

    if spent_amount <= 0:
        transfer.status = "issued"

    elif spent_amount < transfer_amount:
        transfer.status = "partially_spent"

    else:
        transfer.status = "completed"


# =========================================================
# БАЛАНС СОТРУДНИКА (сумма всех выдач минус подтверждённые расходы)
# =========================================================

def get_employee_balance(db, user_internal_id: int) -> float:
    """
    Остаток сотрудника:
    все выдачи минус все записанные расходы.

    Расход списывается сразу, как только сотрудник
    сообщил о нём (не ждём чек).
    """
    user = (
        db.query(TelegramUser)
        .filter(TelegramUser.id == user_internal_id)
        .first()
    )
    if not user:
        return 0.0

    transfers = (
        db.query(MoneyTransfer)
        .filter(
            MoneyTransfer.telegram_user_id == user_internal_id
        )
        .all()
    )
    total_issued = sum(float(t.amount) for t in transfers)

    expenses = (
        db.query(EmployeeExpense)
        .filter(
            EmployeeExpense.telegram_user_id == user.telegram_id
        )
        .all()
    )

    total_spent = 0.0
    for expense in expenses:
        transaction = (
            db.query(Transaction)
            .filter(Transaction.id == expense.transaction_id)
            .first()
        )
        if transaction:
            total_spent += float(transaction.amount)

    balance = total_issued - total_spent
    if balance < 0:
        balance = 0.0

    return balance


def get_employee_issued_spent(db, user_internal_id: int):
    """Возвращает (выдано, потрачено) для сотрудника."""
    user = (
        db.query(TelegramUser)
        .filter(TelegramUser.id == user_internal_id)
        .first()
    )
    if not user:
        return 0.0, 0.0

    transfers = (
        db.query(MoneyTransfer)
        .filter(
            MoneyTransfer.telegram_user_id == user_internal_id
        )
        .all()
    )
    total_issued = sum(float(t.amount) for t in transfers)

    expenses = (
        db.query(EmployeeExpense)
        .filter(
            EmployeeExpense.telegram_user_id == user.telegram_id
        )
        .all()
    )

    total_spent = 0.0
    for expense in expenses:
        transaction = (
            db.query(Transaction)
            .filter(Transaction.id == expense.transaction_id)
            .first()
        )
        if transaction:
            total_spent += float(transaction.amount)

    return total_issued, total_spent


def get_transfer_spent_amount(db, transfer_id: int):
    """
    Сколько уже списано с конкретной выдачи.
    Считаем по чекам/расходам, привязанным к выдаче
    (в т.ч. без файла — расход уже записан).
    """
    receipts = (
        db.query(Receipt)
        .filter(
            Receipt.transfer_id == transfer_id,
            Receipt.receipt_amount.isnot(None),
            Receipt.transaction_id.isnot(None),
        )
        .all()
    )
    return sum(
        float(receipt.receipt_amount) for receipt in receipts
    )


# =========================================================
# МЕНЮ СОТРУДНИКА
# =========================================================

def employee_keyboard():

    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("💸 Сообщить о расходе"),
                KeyboardButton("🧾 Отправить чек")
            ],
            [
                KeyboardButton("💰 Мои расходы"),
                KeyboardButton("💳 Остаток денег")
            ],
            [
                KeyboardButton("📦 Создать запрос"),
                KeyboardButton("📋 Мои запросы")
            ],
            [
                KeyboardButton("👤 Мой профиль")
            ]
        ],
        resize_keyboard=True
    )


def expense_category_keyboard():
    """Категории расхода — кнопками."""
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("🚗 Транспорт"),
                KeyboardButton("🔧 Материалы")
            ],
            [
                KeyboardButton("📎 Канцелярия"),
                KeyboardButton("🍽 Питание")
            ],
            [
                KeyboardButton("📱 Связь и интернет"),
                KeyboardButton("📢 Реклама")
            ],
            [
                KeyboardButton("🏠 Аренда"),
                KeyboardButton("📦 Прочие расходы")
            ],
            [
                KeyboardButton("❌ Отмена")
            ]
        ],
        resize_keyboard=True
    )


def expense_cancel_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("❌ Отмена")]
        ],
        resize_keyboard=True
    )


def expense_description_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("⏭ Без описания")],
            [KeyboardButton("❌ Отмена")]
        ],
        resize_keyboard=True
    )


def yes_no_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("✅ ДА"),
                KeyboardButton("❌ НЕТ")
            ]
        ],
        resize_keyboard=True
    )


# Категории: текст кнопки → значение в БД
EXPENSE_CATEGORY_MAP = {
    "🚗 Транспорт": "Транспорт",
    "🔧 Материалы": "Материалы",
    "📎 Канцелярия": "Канцелярия",
    "🍽 Питание": "Питание",
    "📱 Связь и интернет": "Связь и интернет",
    "📢 Реклама": "Реклама",
    "🏠 Аренда": "Аренда",
    "📦 Прочие расходы": "Прочие расходы",
}


# =========================================================
# МЕНЮ ДИРЕКТОРА
# =========================================================

def director_keyboard():

    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("💳 Выдать деньги"),
                KeyboardButton("📦 Заявки")
            ],
            [
                KeyboardButton("📜 История операций"),
                KeyboardButton("💳 Выданные деньги")
            ],
            [
                KeyboardButton("📊 Отчёты"),
                KeyboardButton("👤 Сотрудники")
            ]
        ],
        resize_keyboard=True
    )


async def start_money_transfer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    db = get_db()

    try:
        employees = (
            db.query(TelegramUser)
            .filter(
                TelegramUser.role == "employee",
                TelegramUser.is_active == True
            )
            .order_by(TelegramUser.full_name)
            .all()
        )

        if not employees:
            await update.message.reply_text(
                "👤 Активных сотрудников нет.",
                reply_markup=director_keyboard()
            )
            return

        # Начинаем процесс выдачи денег
        context.user_data["money_transfer"] = {
            "step": "employee"
        }

        await update.message.reply_text(
            "💳 Выдать деньги\n\n"
            "Сначала выберите сотрудника:",
            reply_markup=employee_selection_keyboard(employees)
        )

    finally:
        db.close()


def employee_selection_keyboard(employees):
    buttons = []

    for employee in employees:
        buttons.append([
            KeyboardButton(
                f"👤 {employee.full_name}"
            )
        ])

    buttons.append([
        KeyboardButton("❌ Отмена")
    ])

    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True
    )


def account_selection_keyboard():
    """Выбор счёта, с которого выдаются деньги (как в веб-форме)."""
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("🏦 Банк"),
                KeyboardButton("💳 Расчётный счёт"),
            ],
            [
                KeyboardButton("❌ Отмена"),
            ],
        ],
        resize_keyboard=True,
    )


# Текст кнопки → значение в БД (как в веб-форме)
ACCOUNT_MAP = {
    "🏦 Банк": "cash",
    "💳 Расчётный счёт": "bank_account",
}

ACCOUNT_LABELS = {
    "cash": "🏦 Банк",
    "bank_account": "💳 Расчётный счёт",
}


async def handle_money_transfer_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str
):
    data = context.user_data.get("money_transfer")

    if not data:
        return False

    # =====================================================
    # ШАГ 1. ВЫБОР СОТРУДНИКА
    # =====================================================

    if data.get("step") == "employee":

        if text == "❌ Отмена":
            context.user_data.pop(
                "money_transfer",
                None
            )

            await update.message.reply_text(
                "❌ Выдача денег отменена.",
                reply_markup=director_keyboard()
            )

            return True

        if not text.startswith("👤 "):
            return True

        employee_name = text[2:].strip()

        db = get_db()

        try:
            employee = (
                db.query(TelegramUser)
                .filter(
                    TelegramUser.full_name == employee_name,
                    TelegramUser.role == "employee",
                    TelegramUser.is_active == True
                )
                .first()
            )

            if not employee:
                await update.message.reply_text(
                    "❌ Сотрудник не найден."
                )
                return True

            # Сохраняем выбранного сотрудника
            data["employee_id"] = employee.id
            data["employee_name"] = employee.full_name
            data["employee_telegram_id"] = employee.telegram_id

            # Переходим к выбору счёта
            data["step"] = "account"

            balance = get_employee_balance(
                db,
                employee.id
            )

            await update.message.reply_text(
                f"👤 Сотрудник: {employee.full_name}\n\n"
                f"💰 Текущий баланс: "
                f"{balance:,.2f} ₽\n\n"
                "Выберите счёт, с которого выдаются деньги:",
                reply_markup=account_selection_keyboard()
            )

        finally:
            db.close()

        return True

    # =====================================================
    # ШАГ 2. ВЫБОР СЧЁТА
    # =====================================================

    if data.get("step") == "account":

        if text == "❌ Отмена":
            context.user_data.pop("money_transfer", None)
            await update.message.reply_text(
                "❌ Выдача денег отменена.",
                reply_markup=director_keyboard()
            )
            return True

        account = ACCOUNT_MAP.get(text)
        if not account:
            await update.message.reply_text(
                "Выберите счёт кнопкой ниже:",
                reply_markup=account_selection_keyboard()
            )
            return True

        data["account"] = account
        data["step"] = "amount"

        account_label = ACCOUNT_LABELS.get(account, account)

        await update.message.reply_text(
            f"👤 Сотрудник: {data['employee_name']}\n"
            f"🏦 Счёт: {account_label}\n\n"
            "Введите сумму, которую нужно выдать:\n\n"
            "Например:\n"
            "50000",
            reply_markup=ReplyKeyboardMarkup(
                [
                    [
                        KeyboardButton("❌ Отмена")
                    ]
                ],
                resize_keyboard=True
            )
        )
        return True

    # =====================================================
    # ШАГ 3. ВВОД СУММЫ
    # =====================================================

    if data.get("step") == "amount":

        if text == "❌ Отмена":

            context.user_data.pop(
                "money_transfer",
                None
            )

            await update.message.reply_text(
                "❌ Выдача денег отменена.",
                reply_markup=director_keyboard()
            )

            return True

        amount = extract_amount(text)

        if amount is None or amount <= 0:

            await update.message.reply_text(
                "❌ Не удалось определить сумму.\n\n"
                "Введите, например:\n"
                "50000"
            )

            return True

        account = data.get("account") or "cash"
        account_label = ACCOUNT_LABELS.get(account, account)

        db = get_db()

        try:
            employee = (
                db.query(TelegramUser)
                .filter(
                    TelegramUser.id == data["employee_id"]
                )
                .first()
            )

            if not employee:

                await update.message.reply_text(
                    "❌ Сотрудник не найден."
                )

                return True

            # =================================================
            # СОЗДАЁМ НОВУЮ ВЫДАЧУ
            # =================================================

            transfer = MoneyTransfer(
                telegram_user_id=employee.id,
                amount=amount,
                account=account,
                purpose="Пополнение баланса",
                comment="Выдача денежных средств сотруднику",
                status="issued",
                created_at=date.today()
            )

            db.add(transfer)
            db.commit()
            db.refresh(transfer)

            # =================================================
            # ПОЛУЧАЕМ НОВЫЙ БАЛАНС
            # =================================================

            balance = get_employee_balance(
                db,
                employee.id
            )

            # =================================================
            # ОТВЕТ ДИРЕКТОРУ
            # =================================================

            await update.message.reply_text(
                "✅ Деньги выданы\n\n"
                f"👤 {employee.full_name}\n"
                f"🏦 Счёт: {account_label}\n"
                f"💰 Выдано: {amount:,.2f} ₽\n"
                f"💳 Теперь у сотрудника: {balance:,.2f} ₽",
                reply_markup=director_keyboard()
            )

            # =================================================
            # УВЕДОМЛЕНИЕ СОТРУДНИКУ
            # =================================================

            try:

                await context.bot.send_message(
                    chat_id=employee.telegram_id,
                    text=(
                        "💰 Вам выдали деньги\n\n"
                        f"Получили: {amount:,.2f} ₽\n"
                        f"Сейчас у вас: {balance:,.2f} ₽\n\n"
                        "Когда купите что-то — нажмите\n"
                        "«💸 Сообщить о расходе»\n"
                        "и отправьте чек."
                    ),
                    reply_markup=employee_keyboard()
                )

            except Exception as error:

                print(
                    "❌ Не удалось уведомить сотрудника:",
                    error
                )

            # =================================================
            # ОЧИЩАЕМ СОСТОЯНИЕ
            # =================================================

            context.user_data.pop(
                "money_transfer",
                None
            )

        except Exception as error:

            db.rollback()

            print(
                "❌ Ошибка выдачи денег:",
                error
            )

            await update.message.reply_text(
                "❌ Не удалось выдать деньги.",
                reply_markup=director_keyboard()
            )

        finally:
            db.close()

        return True

    return False

# =========================================================
# ФИО
# =========================================================

def normalize_full_name(text: str):

    return " ".join(
        text.strip().split()
    )


def is_valid_full_name(text: str):

    parts = text.strip().split()

    return len(parts) >= 2


# =========================================================
# ПОЛЬЗОВАТЕЛЬ TELEGRAM
# =========================================================

def get_or_create_telegram_user(
    telegram_user,
    full_name=None
):

    db = get_db()

    try:

        user = get_user(
            db,
            telegram_user.id
        )

        if user:

            if full_name:

                user.full_name = full_name

                db.commit()
                db.refresh(user)

            return user

        if not full_name:
            return None

        role = "employee"

        if (
            DIRECTOR_TELEGRAM_ID
            and telegram_user.id == DIRECTOR_TELEGRAM_ID
        ):
            role = "director"

        return create_user(
            db=db,
            telegram_id=telegram_user.id,
            full_name=full_name,
            role=role
        )

    finally:

        db.close()


# =========================================================
# КАТЕГОРИЯ
# =========================================================

def detect_category(text: str):

    if not text:
        return "Прочие расходы"

    lower = text.lower()

    transport_words = [
        "бензин",
        "топлив",
        "заправ",
        "такси",
        "автобус",
        "метро",
        "транспорт",
        "парков",
        "мойк",
        "автомоб",
        "машин"
    ]

    for word in transport_words:

        if word in lower:
            return "Транспорт"

    material_words = [
        "материал",
        "стройматериал",
        "краск",
        "инструмент",
        "детал",
        "комплектующ",
        "оборудован"
    ]

    for word in material_words:

        if word in lower:
            return "Материалы"

    office_words = [
        "канцел",
        "бумаг",
        "ручк",
        "тетрад",
        "папк",
        "принтер",
        "картридж"
    ]

    for word in office_words:

        if word in lower:
            return "Канцелярия"

    food_words = [
        "еда",
        "обед",
        "ужин",
        "завтрак",
        "продукт",
        "кафе",
        "ресторан",
        "кофе",
        "вода",
        "питани"
    ]

    for word in food_words:

        if word in lower:
            return "Питание"

    communication_words = [
        "интернет",
        "телефон",
        "связь",
        "мобильн",
        "симкарт"
    ]

    for word in communication_words:

        if word in lower:
            return "Связь и интернет"

    if "аренд" in lower:
        return "Аренда"

    if (
        "зарплат" in lower
        or "аванс" in lower
    ):
        return "Зарплата"

    advertising_words = [
        "реклам",
        "продвижен",
        "таргет"
    ]

    for word in advertising_words:

        if word in lower:
            return "Реклама"

    return "Прочие расходы"


# =========================================================
# ИЗВЛЕЧЕНИЕ СУММЫ
# =========================================================

def extract_amount(text: str):
    """
    Извлекает денежную сумму из обычного текста.

    Поддерживает:
        3500
        3500 ₽
        3500 руб
        3500 рублей
        3 500
        3 500 ₽
        3500,50
        3500.50
        3 500,50 ₽
    """

    if not text:
        return None

    normalized = text.lower().strip()

    # Неразрывный пробел -> обычный
    normalized = normalized.replace("\u00a0", " ")

    # Убираем обозначения валюты
    normalized = normalized.replace("₽", " ")

    normalized = re.sub(
        r"\b(руб(?:\.|лей|ля)?|р\.)(?=\s|$)",
        " ",
        normalized
    )

    # ---------------------------------------------------------
    # Сначала ищем числа с копейками
    # ---------------------------------------------------------

    decimal_matches = re.findall(
        r"(?<!\d)"
        r"\d+(?:[ \t]\d{3})*"
        r"[.,]\d{1,2}"
        r"(?!\d)",
        normalized
    )

    if decimal_matches:
        value = decimal_matches[-1]

        value = (
            value
            .replace(" ", "")
            .replace(",", ".")
        )

        try:
            amount = float(value)

            if amount > 0:
                return amount

        except ValueError:
            pass

    # ---------------------------------------------------------
    # Затем обычные целые числа
    # ---------------------------------------------------------

    integer_matches = re.findall(
        r"(?<!\d)"
        r"\d{1,3}(?:[ \t]\d{3})+"
        r"(?!\d)"
        r"|"
        r"(?<!\d)"
        r"\d+"
        r"(?!\d)",
        normalized
    )

    if not integer_matches:
        return None

    candidates = []

    for value in integer_matches:

        clean_value = value.replace(" ", "")

        try:
            amount = float(clean_value)

            if amount > 0:
                candidates.append(amount)

        except ValueError:
            continue

    if not candidates:
        return None

    return candidates[-1]
# =========================================================
# ПАРСИНГ ДАТЫ ЧЕКА
# =========================================================

def parse_receipt_date(value):

    if not value:
        return date.today()

    if isinstance(value, datetime.datetime):
        return value.date()

    if isinstance(value, date):
        return value


    if isinstance(value, str):

        formats = [
            "%d.%m.%Y",
            "%d-%m-%Y",
            "%Y-%m-%d",
            "%d/%m/%Y"
        ]

        for fmt in formats:

            try:
                return datetime.datetime.strptime(
                    value.strip(),
                    fmt
                ).date()

            except ValueError:
                continue

    return date.today()


# =========================================================
# СОХРАНЕНИЕ ФАЙЛА ЧЕКА
# =========================================================

def save_receipt_file(
    telegram_user_id: int,
    filename: str,
    file_bytes: bytes
):

    user_dir = (
        RECEIPTS_DIR
        / str(telegram_user_id)
    )

    user_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    safe_name = re.sub(
        r"[^a-zA-Zа-яА-ЯёЁ0-9._-]",
        "_",
        filename
    )

    unique_name = (
        f"{uuid.uuid4().hex}_"
        f"{safe_name}"
    )

    filepath = (
        user_dir
        / unique_name
    )

    filepath.write_bytes(
        file_bytes
    )

    return filepath


# =========================================================
# ОШИБКИ
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print(
        "\n❌ ОШИБКА TELEGRAM-БОТА:"
    )

    print(
        context.error
    )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    telegram_user = update.effective_user

    if not telegram_user:
        return

    context.user_data.pop("purchase_request", None)
    context.user_data.pop("waiting_receipt_amount", None)
    context.user_data.pop("pending_expense", None)
    context.user_data.pop("receipt_confirmation", None)
    context.user_data.pop("expense_flow", None)
    context.user_data.pop("money_transfer", None)
    context.user_data.pop("ops_offset", None)

    db = get_db()

    try:

        user = (
            db.query(TelegramUser)
            .filter(
                TelegramUser.telegram_id
                == telegram_user.id
            )
            .first()
        )

        if not user:

            context.user_data[
                "awaiting_full_name"
            ] = True

            await update.message.reply_text(
                "👋 Добро пожаловать в Finance Bot!\n\n"
                "Для регистрации напишите ваше ФИО полностью.\n\n"
                "Например:\n"
                "Иванов Иван Иванович"
            )

            return

        if user.full_name:

            if user.role == "director":

                text = (
                    f"Здравствуйте, {user.full_name}!\n\n"
                    "👑 Вы вошли как директор Finance Bot."
                )

            else:

                text = (
                    f"Здравствуйте, {user.full_name}!\n\n"
                    "👤 Вы вошли как сотрудник Finance Bot."
                )

            await update.message.reply_text(
                text
            )

            await show_main_menu(
                update,
                user
            )

    finally:

        db.close()


# =========================================================
# /ID
# =========================================================

async def my_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.effective_user:
        return

    await update.message.reply_text(
        f"Ваш Telegram ID: "
        f"{update.effective_user.id}"
    )


# =========================================================
# /ME
# =========================================================

async def me(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    telegram_user = update.effective_user

    if not telegram_user:
        return

    user = get_or_create_telegram_user(
        telegram_user
    )

    if not user:

        await update.message.reply_text(
            "👤 Сначала зарегистрируйтесь через /start."
        )

        return

    role_name = (
        "Директор"
        if user.role == "director"
        else "Сотрудник"
    )

    await update.message.reply_text(
        "👤 Ваш профиль\n\n"
        f"Имя: {user.full_name}\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"Роль: {role_name}"
    )


# =========================================================
# СОХРАНЕНИЕ ЧЕКА
# =========================================================

async def handle_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    telegram_user = update.effective_user

    if not telegram_user:
        return

    user = get_or_create_telegram_user(
        telegram_user
    )

    if not user:

        await update.message.reply_text(
            "👤 Сначала зарегистрируйтесь через /start."
        )

        return

    if user.role != "employee":

        await update.message.reply_text(
            "👑 Отправка чеков доступна сотрудникам."
        )

        return

    # =====================================================
    # ПРОВЕРЯЕМ, ЕСТЬ ЛИ ОЖИДАЮЩИЙ РАСХОД
    # =====================================================

    pending_expense = context.user_data.get(
        "pending_expense"
    )

    if not pending_expense:

        await update.message.reply_text(
            "⚠️ Сначала оформите расход "
            "(кнопка «💸 Сообщить о расходе»),\n"
            "потом пришлите чек.",
            reply_markup=employee_keyboard()
        )

        return

    await update.message.reply_text(
        "🔎 Получаю чек..."
    )

    telegram_file = None
    filename = None

    # =====================================================
    # ФОТО
    # =====================================================

    if update.message.photo:

        try:

            photo = update.message.photo[-1]

            telegram_file = (
                await context.bot.get_file(
                    photo.file_id
                )
            )

            filename = (
                f"receipt_"
                f"{uuid.uuid4().hex}.jpg"
            )

        except Exception as error:

            print(
                "Ошибка получения фото:",
                error
            )

            await update.message.reply_text(
                "❌ Не удалось получить фотографию."
            )

            return

    # =====================================================
    # ДОКУМЕНТ
    # =====================================================

    elif update.message.document:

        try:

            document = update.message.document

            telegram_file = (
                await context.bot.get_file(
                    document.file_id
                )
            )

            filename = (
                document.file_name
                or f"receipt_{uuid.uuid4().hex}"
            )

        except Exception as error:

            print(
                "Ошибка получения документа:",
                error
            )

            await update.message.reply_text(
                "❌ Не удалось получить файл."
            )

            return

    else:

        await update.message.reply_text(
            "❌ Отправьте чек фотографией "
            "или файлом."
        )

        return

    # =====================================================
    # СКАЧИВАНИЕ
    # =====================================================

    try:

        file_bytes = (
            await telegram_file.download_as_bytearray()
        )

        filepath = save_receipt_file(
            telegram_user_id=telegram_user.id,
            filename=filename,
            file_bytes=bytes(file_bytes)
        )

    except Exception as error:

        print(
            "Ошибка сохранения файла:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось сохранить файл чека."
        )

        return

    # =====================================================
    # OCR
    # =====================================================

    ocr_text = None
    shop_name = None
    amount = None
    receipt_date = date.today()

    image_extensions = (
        ".jpg",
        ".jpeg",
        ".png",
        ".webp"
    )

    if filepath.suffix.lower() in image_extensions:

        await update.message.reply_text(
            "🔎 Распознаю чек..."
        )

        try:

            result = recognize_receipt(
                str(filepath)
            )

            if isinstance(result, dict):

                ocr_text = result.get(
                    "text"
                )

                shop_name = result.get(
                    "shop_name"
                )

                amount = result.get(
                    "amount"
                )

                receipt_date = parse_receipt_date(
                    result.get("date")
                )

        except Exception as error:

            print(
                "Ошибка OCR:",
                error
            )

    else:

        await update.message.reply_text(
            "📄 Файл сохранён.\n"
            "Автоматическое распознавание "
            "для этого формата пока не выполняется."
        )

    # =====================================================
    # ЕСЛИ OCR ВЕРНУЛ ТЕКСТ, НО НЕ СУММУ
    # =====================================================

    if amount is None and ocr_text:

        amount = extract_amount(
            ocr_text
        )

    # =====================================================
    # ПРИКРЕПЛЯЕМ ЧЕК К УЖЕ ЗАПИСАННОМУ РАСХОДУ
    # (расход списан сразу, чек — только документ)
    # =====================================================

    pending_expense = context.user_data.get("pending_expense")
    if not pending_expense:
        await update.message.reply_text(
            "⚠️ Не найден расход для этого чека.",
            reply_markup=employee_keyboard()
        )
        return

    transfer_id = pending_expense.get("transfer_id")
    transaction_id = pending_expense.get("transaction_id")
    existing_receipt_id = pending_expense.get("receipt_id")

    db = get_db()
    try:
        # Обновляем заглушку чека или создаём новый
        if existing_receipt_id:
            receipt = (
                db.query(Receipt)
                .filter(Receipt.id == existing_receipt_id)
                .first()
            )
            if receipt:
                receipt.filename = filename
                receipt.filepath = str(filepath)
                receipt.ocr_text = ocr_text
                receipt.shop_name = shop_name
                if amount is not None:
                    receipt.receipt_amount = float(amount)
                receipt.receipt_date = receipt_date
                if transaction_id:
                    receipt.transaction_id = transaction_id
                db.commit()
            else:
                receipt = None
        else:
            receipt = None

        if not receipt:
            receipt = Receipt(
                transaction_id=transaction_id,
                telegram_user_id=telegram_user.id,
                transfer_id=transfer_id,
                filename=filename,
                filepath=str(filepath),
                ocr_text=ocr_text,
                shop_name=shop_name,
                receipt_amount=(
                    float(amount) if amount is not None
                    else pending_expense.get("amount")
                ),
                receipt_date=receipt_date,
                uploaded_at=date.today(),
            )
            db.add(receipt)
            db.commit()
            db.refresh(receipt)

        context.user_data.pop("pending_expense", None)

        shop_text = receipt.shop_name or "—"
        await update.message.reply_text(
            "✅ Чек прикреплён к расходу.\n\n"
            f"🧾 Чек №{receipt.id}\n"
            f"🏪 {shop_text}\n"
            f"📄 {filename}",
            reply_markup=employee_keyboard()
        )

    except Exception as error:
        db.rollback()
        print("Ошибка сохранения чека:", error)
        await update.message.reply_text(
            "❌ Не удалось сохранить чек.",
            reply_markup=employee_keyboard()
        )
    finally:
        db.close()


# =========================================================
# СОЗДАНИЕ ОПЕРАЦИИ ПО ЧЕКУ
# =========================================================

# =========================================================
# СОЗДАНИЕ ОПЕРАЦИИ ПО ПОДТВЕРЖДЁННОМУ ЧЕКУ
# =========================================================

async def create_transaction_from_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    receipt_id: int,
    amount: float,
    force_confirmed: bool = False
):

    telegram_user = update.effective_user

    if not telegram_user:
        return False

    pending_expense = context.user_data.get(
        "pending_expense"
    )

    if not pending_expense:

        await update.message.reply_text(
            "⚠️ Не найден ожидающий расход.\n\n"
            "Расход не был записан."
        )

        return False

    db = get_db()

    try:

        # =================================================
        # ПОЛУЧАЕМ ЧЕК
        # =================================================

        receipt = (
            db.query(Receipt)
            .filter(
                Receipt.id == receipt_id
            )
            .first()
        )

        if not receipt:

            await update.message.reply_text(
                "❌ Чек не найден."
            )

            return False

        # =================================================
        # ЗАЩИТА ОТ ПОВТОРНОЙ ЗАПИСИ
        # =================================================

        if receipt.transaction_id:

            await update.message.reply_text(
                "⚠️ Этот чек уже был обработан.\n\n"
                f"🧾 Чек №{receipt.id}\n"
                f"💰 Сумма: "
                f"{float(receipt.receipt_amount or 0):,.2f} ₽\n\n"
                "Повторно добавлять этот расход "
                "я не буду."
            )

            return False

        # =================================================
        # ПОЛЬЗОВАТЕЛЬ
        # =================================================

        user = (
            db.query(TelegramUser)
            .filter(
                TelegramUser.telegram_id
                == telegram_user.id
            )
            .first()
        )

        if not user:

            await update.message.reply_text(
                "❌ Пользователь не найден."
            )

            return False

        # =================================================
        # СУММА
        # =================================================

        amount = float(amount)

        if amount <= 0:

            await update.message.reply_text(
                "❌ Сумма должна быть больше нуля."
            )

            return False

        # =================================================
        # СРАВНЕНИЕ С СУММОЙ, УКАЗАННОЙ СОТРУДНИКОМ
        # =================================================

        declared_amount = float(
            pending_expense["amount"]
        )

        difference = abs(
            amount - declared_amount
        )

        # Если сумма отличается и сотрудник ещё
        # не подтвердил её вручную
        if difference > 0.01 and not force_confirmed:

            receipt.receipt_amount = amount

            context.user_data[
                "receipt_confirmation"
            ] = {
                "receipt_id": receipt.id,
                "receipt_amount": amount,
                "declared_amount": declared_amount
            }

            db.commit()

            await update.message.reply_text(
                "⚠️ Сумма в чеке отличается "
                "от указанной суммы.\n\n"
                f"📝 Вы указали: "
                f"{declared_amount:,.2f} ₽\n"
                f"🧾 В чеке: "
                f"{amount:,.2f} ₽\n"
                f"📊 Разница: "
                f"{difference:,.2f} ₽\n\n"
                "Подтвердите сумму из чека кнопкой:",
                reply_markup=yes_no_keyboard()
            )

            return False

        # =================================================
        # ПОЛУЧАЕМ MONEY TRANSFER
        # =================================================

        transfer = None

        transfer_id = pending_expense.get(
            "transfer_id"
        )

        if transfer_id:

            transfer = (
                db.query(MoneyTransfer)
                .filter(
                    MoneyTransfer.id
                    == transfer_id
                )
                .first()
            )

        # =================================================
        # ПРОВЕРЯЕМ ОСТАТОК
        # =================================================

        if transfer:

            already_spent = (
                get_transfer_spent_amount(
                    db,
                    transfer.id
                )
            )

            remaining = (
                float(transfer.amount)
                - already_spent
            )

            if remaining < 0:
                remaining = 0

            if amount > remaining + 0.01:

                await update.message.reply_text(
                    "⚠️ Расход не записан.\n\n"
                    "Сумма чека превышает остаток "
                    "выданных денег.\n\n"
                    f"💳 Выдано: "
                    f"{float(transfer.amount):,.2f} ₽\n"
                    f"💸 Уже подтверждено: "
                    f"{already_spent:,.2f} ₽\n"
                    f"💵 Остаток: "
                    f"{remaining:,.2f} ₽\n"
                    f"🧾 Сумма чека: "
                    f"{amount:,.2f} ₽\n\n"
                    "Проверьте сумму или обратитесь "
                    "к директору."
                )

                return False

        # =================================================
        # КАТЕГОРИЯ
        # =================================================

        category = pending_expense.get(
            "category"
        ) or detect_category(
            pending_expense.get(
                "description",
                ""
            )
        )

        # =================================================
        # ОПИСАНИЕ
        # =================================================

        description = pending_expense.get(
            "description"
        )

        if not description:

            if receipt.shop_name:

                description = (
                    f"Покупка: "
                    f"{receipt.shop_name}"
                )

            else:

                description = "Расход по чеку"

        # =================================================
        # TRANSACTION
        # =================================================

        transaction = Transaction(
            type="expense",
            category=category,
            amount=amount,
            description=description,
            date=(
                receipt.receipt_date
                or date.today()
            )
        )

        db.add(transaction)

        db.flush()

        # =================================================
        # EMPLOYEE EXPENSE
        # =================================================

        employee_expense = EmployeeExpense(
            telegram_user_id=telegram_user.id,
            transaction_id=transaction.id,
            description=description
        )

        db.add(
            employee_expense
        )

        # =================================================
        # RECEIPT
        # =================================================

        receipt.transaction_id = (
            transaction.id
        )

        receipt.receipt_amount = (
            amount
        )

        db.flush()

        # =================================================
        # MONEY TRANSFER
        # =================================================

        transfer_message = ""
        spent_amount = 0.0
        remaining = 0.0

        if transfer:
            update_transfer_status(db, transfer)
            spent_amount = get_transfer_spent_amount(
                db, transfer.id
            )
            remaining = float(transfer.amount) - spent_amount
            if remaining < 0:
                remaining = 0

        # receipt уже привязан к transaction → баланс уже с учётом этого расхода
        new_balance = get_employee_balance(db, user.id)
        transfer_message = (
            f"\n\n💳 Осталось: {new_balance:,.2f} ₽"
        )

        # =================================================
        # СОХРАНЯЕМ ВСЁ
        # =================================================

        db.commit()

        # =================================================
        # ОЧИЩАЕМ СОСТОЯНИЕ
        # =================================================

        context.user_data.pop(
            "pending_expense",
            None
        )

        context.user_data.pop(
            "waiting_receipt_amount",
            None
        )

        context.user_data.pop(
            "receipt_confirmation",
            None
        )

        # =================================================
        # ОТВЕТ
        # =================================================

        shop_text = (
            receipt.shop_name
            if receipt.shop_name
            else "Не определён"
        )

        date_text = (
            receipt.receipt_date.strftime(
                "%d.%m.%Y"
            )
            if receipt.receipt_date
            else "Не определена"
        )

        await update.message.reply_text(
            "✅ Расход подтверждён и записан!\n\n"
            f"👤 Сотрудник: "
            f"{user.full_name}\n"
            f"🏪 Магазин: "
            f"{shop_text}\n"
            f"💰 Сумма: "
            f"{amount:,.2f} ₽\n"
            f"📅 Дата: "
            f"{date_text}\n"
            f"📂 Категория: "
            f"{category}\n"
            f"🧾 Чек №{receipt.id}\n"
            f"💸 Операция №{transaction.id}"
            f"{transfer_message}",
            reply_markup=employee_keyboard()
        )

        # =================================================
        # УВЕДОМЛЕНИЕ ДИРЕКТОРУ О РАСХОДЕ
        # =================================================

        if DIRECTOR_TELEGRAM_ID:

            director_expense_text = (
                "💸 НОВЫЙ РАСХОД СОТРУДНИКА\n\n"
                f"👤 Сотрудник: "
                f"{user.full_name}\n"
                f"💰 Сумма: "
                f"{amount:,.2f} ₽\n"
                f"📂 Категория: "
                f"{category}\n"
                f"📝 Описание: "
                f"{description}\n"
                f"🏪 Магазин: "
                f"{shop_text}\n"
                f"📅 Дата: "
                f"{date_text}\n"
                f"🧾 Чек №{receipt.id}\n"
                f"💸 Операция №{transaction.id}"
            )

            if transfer:

                director_expense_text += (
                    "\n\n"
                    "💳 Выданные деньги:\n"
                    f"Выдано: "
                    f"{float(transfer.amount):,.2f} ₽\n"
                    f"Потрачено: "
                    f"{spent_amount:,.2f} ₽\n"
                    f"Остаток: "
                    f"{remaining:,.2f} ₽"
                )

            try:

                await context.bot.send_message(
                    chat_id=DIRECTOR_TELEGRAM_ID,
                    text=director_expense_text
                )

            except Exception as error:

                print(
                    "Не удалось отправить "
                    "уведомление директору о расходе:",
                    error
                )
        return True

    except Exception as error:

        db.rollback()

        print(
            "Ошибка создания операции по чеку:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось создать расход.\n\n"
            f"Ошибка: {error}"
        )

        return False

    finally:

        db.close()

# =========================================================
# МОИ РАСХОДЫ
# =========================================================

async def my_expenses(
    update: Update,
    user
):

    db = get_db()

    try:

        expenses = (
            db.query(EmployeeExpense)
            .filter(
                EmployeeExpense.telegram_user_id
                == user.telegram_id
            )
            .order_by(
                EmployeeExpense.id.desc()
            )
            .limit(10)
            .all()
        )

        if not expenses:

            await update.message.reply_text(
                "💰 У вас пока нет расходов."
            )

            return

        total = 0

        lines = [
            "💰 Ваши последние расходы:\n"
        ]

        for expense in expenses:

            transaction = (
                db.query(Transaction)
                .filter(
                    Transaction.id
                    == expense.transaction_id
                )
                .first()
            )

            if not transaction:
                continue

            total += float(
                transaction.amount
            )

            lines.append(
                f"• {transaction.amount:,.2f} ₽ — "
                f"{transaction.category}\n"
                f"  {transaction.description or ''}\n"
                f"  {transaction.date.strftime('%d.%m.%Y')}"
            )

        lines.append(
            f"\n💵 Всего: {total:,.2f} ₽"
        )

        await update.message.reply_text(
            "\n".join(lines)
        )

    finally:

        db.close()


# =========================================================
# МОИ ЗАПРОСЫ
# =========================================================

STATUS_NAMES = {
    "new": "🟡 Новая",
    "approved": "🟢 Одобрена",
    "rejected": "🔴 Отклонена",
    "completed": "✅ Выполнена",
    "not_completed": "❌ Не выполнена",
    "ordered": "🔵 Заказана",
    "received": "📦 Получена",
}


async def my_purchase_requests(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user
):

    db = get_db()

    try:

        requests = (
            db.query(PurchaseRequest)
            .filter(
                PurchaseRequest.telegram_user_id
                == user.id
            )
            .order_by(
                PurchaseRequest.id.desc()
            )
            .all()
        )

        if not requests:

            await update.message.reply_text(
                "📋 У вас пока нет заявок.",
                reply_markup=employee_keyboard()
            )

            return

        for request in requests:

            status = STATUS_NAMES.get(
                request.status,
                request.status
            )

            created_text = (
                request.created_at.strftime(
                    "%d.%m.%Y"
                )
                if request.created_at
                else "—"
            )

            amount_text = ""
            if hasattr(request, "estimated_amount") and request.estimated_amount:
                amount_text = f"\n💰 Сумма: {float(request.estimated_amount):,.2f} ₽"
            else:
                # пробуем взять из items
                items = (
                    db.query(PurchaseRequestItem)
                    .filter(PurchaseRequestItem.request_id == request.id)
                    .all()
                )
                if items:
                    total = sum(
                        float(i.quantity or 1) * float(i.estimated_price or 0)
                        for i in items
                    )
                    if total > 0:
                        amount_text = f"\n💰 Сумма: {total:,.2f} ₽"

            text = (
                f"📌 {request.number}\n"
                f"📝 {request.title}\n"
                f"Статус: {status}\n"
                f"Дата: {created_text}"
                f"{amount_text}"
            )

            # Если одобрена — показываем кнопки «Выполнено / Не выполнено»
            if request.status == "approved":
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Выполнено",
                            callback_data=f"pr_done:{request.id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Не выполнено",
                            callback_data=f"pr_notdone:{request.id}"
                        ),
                    ]
                ])
                await update.message.reply_text(
                    text,
                    reply_markup=keyboard
                )
            else:
                await update.message.reply_text(text)

        await update.message.reply_text(
            "— конец списка —",
            reply_markup=employee_keyboard()
        )

    finally:

        db.close()


# =========================================================
# СОЗДАНИЕ ЗАЯВКИ (упрощённое: сумма + на что)
# =========================================================

async def start_purchase_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "purchase_request"
    ] = {
        "step": "amount"
    }

    await update.message.reply_text(
        "📦 Создание заявки\n\n"
        "Напишите, сколько денег нужно и на что.\n\n"
        "Пример:\n"
        "15000 на бензин\n"
        "или\n"
        "50000 на материалы для ремонта",
        reply_markup=expense_cancel_keyboard()
    )


# =========================================================
# ШАГИ ЗАЯВКИ
# =========================================================

async def handle_purchase_request_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user,
    text: str
):
    data = context.user_data.get("purchase_request")

    if not data:
        return False

    if text == "❌ Отмена":
        context.user_data.pop("purchase_request", None)
        await update.message.reply_text(
            "❌ Заявка отменена.",
            reply_markup=employee_keyboard()
        )
        return True

    step = data.get("step")

    # ---------- ШАГ: СУММА + НАЗНАЧЕНИЕ (одним сообщением) ----------
    if step == "amount":
        amount = extract_amount(text)

        if amount is None or amount <= 0:
            await update.message.reply_text(
                "❌ Не понял сумму.\n\n"
                "Напишите, например:\n"
                "15000 на бензин",
                reply_markup=expense_cancel_keyboard()
            )
            return True

        # Убираем сумму из текста — остаётся «на что»
        purpose = text
        purpose = re.sub(
            r"(?<!\d)\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?",
            " ",
            purpose,
        )
        purpose = re.sub(
            r"\b(руб(?:\.|лей|ля|ль)?|р\.?|₽)\b",
            " ",
            purpose,
            flags=re.IGNORECASE,
        )
        purpose = re.sub(r"\s+", " ", purpose).strip(" ,.-")

        if not purpose:
            # Сумма есть, но «на что» нет — спрашиваем отдельно
            data["amount"] = float(amount)
            data["step"] = "purpose"
            await update.message.reply_text(
                f"💰 Сумма: {amount:,.2f} ₽\n\n"
                "Напишите, на что нужны деньги:",
                reply_markup=expense_cancel_keyboard()
            )
            return True

        data["amount"] = float(amount)
        data["purpose"] = purpose
        data["title"] = purpose
        data["description"] = purpose

        await save_purchase_request(update, context, user)
        return True

    # ---------- ШАГ: ТОЛЬКО «НА ЧТО» ----------
    if step == "purpose":
        purpose = text.strip()
        if not purpose:
            await update.message.reply_text(
                "❌ Напишите, на что нужны деньги.",
                reply_markup=expense_cancel_keyboard()
            )
            return True

        data["purpose"] = purpose
        data["title"] = purpose
        data["description"] = purpose

        await save_purchase_request(update, context, user)
        return True

    return False


# =========================================================
# СОХРАНЕНИЕ ЗАЯВКИ
# =========================================================

async def save_purchase_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user
):

    data = context.user_data.get(
        "purchase_request"
    )

    if not data:
        return

    amount = float(data["amount"])
    purpose = data.get("purpose") or data.get("title") or "Заявка"

    db = get_db()

    try:

        purchase_request = PurchaseRequest(
            number="TEMP",
            telegram_user_id=user.id,
            title=purpose,
            description=purpose,
            category="Без категории",
            priority="normal",
            status="new"
        )

        db.add(purchase_request)
        db.flush()

        purchase_request.number = (
            f"З-{purchase_request.id:06d}"
        )

        # Сохраняем сумму как один item
        item = PurchaseRequestItem(
            request_id=purchase_request.id,
            product_name=purpose,
            quantity=1,
            unit="шт.",
            estimated_price=amount
        )

        db.add(item)
        db.commit()
        db.refresh(purchase_request)

        # =================================================
        # ОТВЕТ СОТРУДНИКУ
        # =================================================

        await update.message.reply_text(
            "✅ Заявка создана!\n\n"
            f"📌 Номер: {purchase_request.number}\n"
            f"💰 Сумма: {amount:,.2f} ₽\n"
            f"📝 На что: {purpose}\n\n"
            "Заявка отправлена директору.",
            reply_markup=employee_keyboard()
        )

        # =================================================
        # УВЕДОМЛЕНИЕ ДИРЕКТОРУ С КНОПКАМИ
        # =================================================

        if DIRECTOR_TELEGRAM_ID:

            notification_text = (
                "📦 НОВАЯ ЗАЯВКА\n\n"
                f"📌 Номер: {purchase_request.number}\n"
                f"👤 Сотрудник: {user.full_name}\n"
                f"💰 Сумма: {amount:,.2f} ₽\n"
                f"📝 На что: {purpose}"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Одобрить",
                        callback_data=f"pr_approve:{purchase_request.id}"
                    ),
                    InlineKeyboardButton(
                        "❌ Отказать",
                        callback_data=f"pr_reject:{purchase_request.id}"
                    ),
                ]
            ])

            try:

                await context.bot.send_message(
                    chat_id=DIRECTOR_TELEGRAM_ID,
                    text=notification_text,
                    reply_markup=keyboard
                )

            except Exception as error:

                print(
                    "Не удалось отправить "
                    "уведомление директору:",
                    error
                )

    except Exception as error:

        db.rollback()

        print(
            "Ошибка создания заявки:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось создать заявку.\n\n"
            f"Ошибка: {error}",
            reply_markup=employee_keyboard()
        )

    finally:

        db.close()

        context.user_data.pop(
            "purchase_request",
            None
        )


# =========================================================
# CALLBACK: ОДОБРИТЬ / ОТКАЗАТЬ / ВЫПОЛНЕНО
# =========================================================

async def handle_purchase_request_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data or ""
    if ":" not in data:
        return

    action, raw_id = data.split(":", 1)
    try:
        request_id = int(raw_id)
    except ValueError:
        return

    db = get_db()

    try:
        purchase_request = (
            db.query(PurchaseRequest)
            .filter(PurchaseRequest.id == request_id)
            .first()
        )

        if not purchase_request:
            await query.edit_message_text("❌ Заявка не найдена.")
            return

        employee = (
            db.query(TelegramUser)
            .filter(TelegramUser.id == purchase_request.telegram_user_id)
            .first()
        )

        # Сумма из item
        item = (
            db.query(PurchaseRequestItem)
            .filter(PurchaseRequestItem.request_id == request_id)
            .first()
        )
        amount = float(item.estimated_price) if item and item.estimated_price else 0.0
        purpose = purchase_request.title or "Заявка"

        # ---------- ОДОБРИТЬ ----------
        if action == "pr_approve":
            if purchase_request.status != "new":
                await query.edit_message_text(
                    f"⚠️ Заявка {purchase_request.number} уже обработана "
                    f"(статус: {STATUS_NAMES.get(purchase_request.status, purchase_request.status)})."
                )
                return

            if not employee:
                await query.edit_message_text("❌ Сотрудник не найден.")
                return

            # Создаём выдачу денег (счёт по умолчанию — Банк)
            transfer = MoneyTransfer(
                telegram_user_id=employee.id,
                amount=amount,
                account="cash",
                purpose=purpose,
                comment=f"По заявке {purchase_request.number}",
                status="issued",
                created_at=date.today()
            )
            db.add(transfer)

            purchase_request.status = "approved"
            db.commit()

            balance = get_employee_balance(db, employee.id)

            await query.edit_message_text(
                f"✅ Заявка {purchase_request.number} одобрена.\n\n"
                f"👤 {employee.full_name}\n"
                f"💰 Выдано: {amount:,.2f} ₽\n"
                f"📝 {purpose}\n"
                f"💳 Баланс сотрудника: {balance:,.2f} ₽"
            )

            # Уведомляем сотрудника
            try:
                await context.bot.send_message(
                    chat_id=employee.telegram_id,
                    text=(
                        f"✅ Ваша заявка {purchase_request.number} одобрена!\n\n"
                        f"💰 Вам выдано: {amount:,.2f} ₽\n"
                        f"📝 На что: {purpose}\n"
                        f"💳 Сейчас у вас: {balance:,.2f} ₽\n\n"
                        "Когда выполните — откройте «📋 Мои запросы» "
                        "и нажмите «Выполнено»."
                    ),
                    reply_markup=employee_keyboard()
                )
            except Exception as error:
                print("Не удалось уведомить сотрудника:", error)

            return

        # ---------- ОТКАЗАТЬ ----------
        if action == "pr_reject":
            if purchase_request.status != "new":
                await query.edit_message_text(
                    f"⚠️ Заявка {purchase_request.number} уже обработана "
                    f"(статус: {STATUS_NAMES.get(purchase_request.status, purchase_request.status)})."
                )
                return

            purchase_request.status = "rejected"
            db.commit()

            await query.edit_message_text(
                f"❌ Заявка {purchase_request.number} отклонена.\n\n"
                f"👤 {employee.full_name if employee else '—'}\n"
                f"💰 {amount:,.2f} ₽\n"
                f"📝 {purpose}"
            )

            if employee:
                try:
                    await context.bot.send_message(
                        chat_id=employee.telegram_id,
                        text=(
                            f"❌ Ваша заявка {purchase_request.number} отклонена.\n\n"
                            f"💰 Сумма: {amount:,.2f} ₽\n"
                            f"📝 На что: {purpose}"
                        ),
                        reply_markup=employee_keyboard()
                    )
                except Exception as error:
                    print("Не удалось уведомить сотрудника:", error)

            return

        # ---------- ВЫПОЛНЕНО (сотрудник) ----------
        if action == "pr_done":
            if purchase_request.status != "approved":
                await query.edit_message_text(
                    f"⚠️ Нельзя отметить: статус сейчас "
                    f"{STATUS_NAMES.get(purchase_request.status, purchase_request.status)}."
                )
                return

            purchase_request.status = "completed"
            db.commit()

            await query.edit_message_text(
                f"✅ Заявка {purchase_request.number} отмечена как выполненная.\n\n"
                f"💰 {amount:,.2f} ₽\n"
                f"📝 {purpose}"
            )

            if DIRECTOR_TELEGRAM_ID:
                try:
                    await context.bot.send_message(
                        chat_id=DIRECTOR_TELEGRAM_ID,
                        text=(
                            f"✅ Заявка {purchase_request.number} выполнена\n\n"
                            f"👤 {employee.full_name if employee else '—'}\n"
                            f"💰 {amount:,.2f} ₽\n"
                            f"📝 {purpose}"
                        )
                    )
                except Exception as error:
                    print("Не удалось уведомить директора:", error)

            return

        # ---------- НЕ ВЫПОЛНЕНО (сотрудник) ----------
        if action == "pr_notdone":
            if purchase_request.status != "approved":
                await query.edit_message_text(
                    f"⚠️ Нельзя отметить: статус сейчас "
                    f"{STATUS_NAMES.get(purchase_request.status, purchase_request.status)}."
                )
                return

            purchase_request.status = "not_completed"
            db.commit()

            await query.edit_message_text(
                f"❌ Заявка {purchase_request.number} отмечена как не выполненная.\n\n"
                f"💰 {amount:,.2f} ₽\n"
                f"📝 {purpose}"
            )

            if DIRECTOR_TELEGRAM_ID:
                try:
                    await context.bot.send_message(
                        chat_id=DIRECTOR_TELEGRAM_ID,
                        text=(
                            f"❌ Заявка {purchase_request.number} НЕ выполнена\n\n"
                            f"👤 {employee.full_name if employee else '—'}\n"
                            f"💰 {amount:,.2f} ₽\n"
                            f"📝 {purpose}"
                        )
                    )
                except Exception as error:
                    print("Не удалось уведомить директора:", error)

            return

    except Exception as error:
        db.rollback()
        print("Ошибка callback заявки:", error)
        try:
            await query.edit_message_text("❌ Ошибка обработки заявки.")
        except Exception:
            pass
    finally:
        db.close()


# =========================================================
# ЗАЯВКИ ДИРЕКТОРА
# =========================================================

async def director_purchase_requests(
    update: Update
):

    db = get_db()

    try:

        requests = (
            db.query(PurchaseRequest)
            .order_by(
                PurchaseRequest.id.desc()
            )
            .limit(20)
            .all()
        )

        if not requests:

            await update.message.reply_text(
                "📦 Новых заявок пока нет.",
                reply_markup=director_keyboard()
            )

            return

        for request in requests:

            employee = (
                db.query(TelegramUser)
                .filter(
                    TelegramUser.id
                    == request.telegram_user_id
                )
                .first()
            )

            employee_name = (
                employee.full_name
                if employee
                else "Неизвестный сотрудник"
            )

            status = STATUS_NAMES.get(
                request.status,
                request.status
            )

            item = (
                db.query(PurchaseRequestItem)
                .filter(PurchaseRequestItem.request_id == request.id)
                .first()
            )
            amount = float(item.estimated_price) if item and item.estimated_price else 0.0

            text = (
                f"📌 {request.number}\n"
                f"👤 {employee_name}\n"
                f"💰 {amount:,.2f} ₽\n"
                f"📝 {request.title}\n"
                f"Статус: {status}"
            )

            # Для новых — кнопки одобрить/отказать
            if request.status == "new":
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Одобрить",
                            callback_data=f"pr_approve:{request.id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Отказать",
                            callback_data=f"pr_reject:{request.id}"
                        ),
                    ]
                ])
                await update.message.reply_text(text, reply_markup=keyboard)
            else:
                await update.message.reply_text(text)

        await update.message.reply_text(
            "— конец списка —",
            reply_markup=director_keyboard()
        )

    finally:

        db.close()


# =========================================================
# ИСТОРИЯ ОПЕРАЦИЙ (вместо Чеков)
# =========================================================

async def director_operations_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    offset: int = 0
):
    PAGE_SIZE = 10

    db = get_db()

    try:
        # Сортировка по дате (новые сверху), потом по id
        transactions = (
            db.query(Transaction)
            .order_by(
                Transaction.date.desc(),
                Transaction.id.desc()
            )
            .offset(offset)
            .limit(PAGE_SIZE)
            .all()
        )

        total_count = db.query(Transaction).count()

        if not transactions and offset == 0:
            await update.message.reply_text(
                "📜 Операций пока нет.",
                reply_markup=director_keyboard()
            )
            return

        if not transactions:
            await update.message.reply_text(
                "Больше операций нет.",
                reply_markup=director_keyboard()
            )
            return

        lines = [
            f"📜 История операций "
            f"({offset + 1}–{offset + len(transactions)} из {total_count}):\n"
        ]

        for t in transactions:
            type_emoji = "💰" if t.type == "income" else "💸"
            type_name = "Доход" if t.type == "income" else "Расход"
            date_text = (
                t.date.strftime("%d.%m.%Y")
                if t.date
                else "—"
            )
            lines.append(
                f"{type_emoji} {type_name}: {float(t.amount):,.2f} ₽\n"
                f"  📂 {t.category or '—'}\n"
                f"  📝 {t.description or '—'}\n"
                f"  📅 {date_text}"
            )

        text = "\n\n".join(lines)

        # Кнопка «Ещё», если есть следующие
        has_more = (offset + PAGE_SIZE) < total_count

        if has_more:
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📄 Ещё",
                        callback_data=f"ops_more:{offset + PAGE_SIZE}"
                    )
                ]
            ])
            await update.message.reply_text(
                text,
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text(
                text + "\n\n— все операции показаны —",
                reply_markup=director_keyboard()
            )

        context.user_data["ops_offset"] = offset

    finally:
        db.close()


async def handle_ops_more_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data or ""
    if not data.startswith("ops_more:"):
        return

    try:
        offset = int(data.split(":", 1)[1])
    except ValueError:
        return

    # Отправляем следующее сообщение (не edit — удобнее для истории)
    # Создаём фейковый update с message для reply
    class FakeMessage:
        def __init__(self, chat_id, bot):
            self.chat_id = chat_id
            self._bot = bot

        async def reply_text(self, text, reply_markup=None):
            await self._bot.send_message(
                chat_id=self.chat_id,
                text=text,
                reply_markup=reply_markup
            )

    fake_update = type("obj", (object,), {
        "message": FakeMessage(query.message.chat_id, context.bot)
    })()

    await director_operations_history(fake_update, context, offset=offset)


# =========================================================
# ВЫДАННЫЕ ДЕНЬГИ
# =========================================================

async def director_money_transfers(
    update: Update
):

    db = get_db()

    try:

        transfers = (
            db.query(MoneyTransfer)
            .order_by(
                MoneyTransfer.id.desc()
            )
            .limit(20)
            .all()
        )

        if not transfers:

            await update.message.reply_text(
                "💳 Выданных денег пока нет.",
                reply_markup=director_keyboard()
            )

            return

        lines = [
            "💳 Выданные деньги:\n"
        ]

        for transfer in transfers:

            employee = (
                db.query(TelegramUser)
                .filter(
                    TelegramUser.id
                    == transfer.telegram_user_id
                )
                .first()
            )

            employee_name = (
                employee.full_name
                if employee
                else "Неизвестный сотрудник"
            )

            spent_amount = (
                get_transfer_spent_amount(
                    db,
                    transfer.id
                )
            )

            remaining = (
                float(transfer.amount)
                - spent_amount
            )

            if remaining < 0:
                remaining = 0

            if spent_amount == 0:

                status_text = "🟡 Выдано"

            elif remaining > 0:

                status_text = "🔵 Частично использовано"

            else:

                status_text = "🟢 Закрыто"

            account_raw = getattr(transfer, "account", None) or "cash"
            account_label = ACCOUNT_LABELS.get(account_raw, account_raw)

            lines.append(
                f"👤 {employee_name}\n"
                f"🏦 Счёт: {account_label}\n"
                f"💰 Выдано: "
                f"{transfer.amount:,.2f} ₽\n"
                f"💸 Потрачено: "
                f"{spent_amount:,.2f} ₽\n"
                f"💵 Остаток: "
                f"{remaining:,.2f} ₽\n"
                f"📝 {transfer.purpose}\n"
                f"{status_text}\n"
            )

        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=director_keyboard()
        )

    finally:

        db.close()


# =========================================================
# СОТРУДНИКИ
# =========================================================

async def director_employees(
    update: Update
):

    db = get_db()

    try:

        employees = (
            db.query(TelegramUser)
            .filter(
                TelegramUser.role == "employee",
                TelegramUser.is_active == True
            )
            .order_by(
                TelegramUser.full_name
            )
            .all()
        )

        if not employees:

            await update.message.reply_text(
                "👤 Активных сотрудников нет.",
                reply_markup=director_keyboard()
            )

            return

        lines = [
            "👤 Сотрудники:\n"
        ]

        for employee in employees:
            issued, spent = get_employee_issued_spent(db, employee.id)
            balance = max(issued - spent, 0.0)
            lines.append(
                f"• {employee.full_name}\n"
                f"  💰 Выдано: {issued:,.2f} ₽\n"
                f"  💸 Потрачено: {spent:,.2f} ₽\n"
                f"  💳 Остаток: {balance:,.2f} ₽"
            )

        lines.append(
            "\nЧтобы выдать деньги — нажмите "
            "«💳 Выдать деньги» и выберите сотрудника "
            "из списка кнопок."
        )

        await update.message.reply_text(
            "\n".join(lines),
            reply_markup=director_keyboard()
        )

    finally:

        db.close()


# =========================================================
# ПОИСК СОТРУДНИКА В ТЕКСТЕ
# =========================================================

def find_employee_in_text(
    db,
    text: str
):

    lower = text.lower()

    employees = (
        db.query(TelegramUser)
        .filter(
            TelegramUser.role == "employee",
            TelegramUser.is_active == True
        )
        .all()
    )

    # Сначала точное совпадение ФИО
    for employee in employees:

        full_name = (
            employee.full_name or ""
        ).lower().strip()

        if (
            full_name
            and full_name in lower
        ):
            return employee

    # Затем ищем фамилию/имя
    for employee in employees:

        full_name = (
            employee.full_name or ""
        ).lower().strip()

        parts = full_name.split()

        if not parts:
            continue

        surname = parts[0]
        first_name = (
            parts[1]
            if len(parts) > 1
            else ""
        )

        # Убираем окончания для случаев:
        # Иванов / Иванову / Иванова
        surname_stem = re.sub(
            r"(ов|ова|ову|ев|ева|еву|ин|ина|ину)$",
            "",
            surname
        )

        words = re.findall(
            r"[а-яёa-z]+",
            lower
        )

        for word in words:

            if (
                len(word) >= 4
                and (
                    word.startswith(
                        surname_stem
                    )
                    or surname.startswith(word)
                    or word.startswith(surname[:4])
                )
            ):
                return employee

        if (
            first_name
            and first_name in lower
        ):
            return employee

    return None


# =========================================================
# ТЕКСТ ДИРЕКТОРА
# =========================================================

async def handle_director_text(
    update: Update,
    text: str
):

    lower = text.lower().strip()

    # Нормализуем ё/е, чтобы:
    # перевел = перевёл
    # выдал = выдал
    normalized = lower.replace("ё", "е")

    # =====================================================
    # ВЫДАЧА ДЕНЕГ СОТРУДНИКУ
    # =====================================================

    transfer_words = [
        "скинул",
        "скинула",
        "скинуть",

        "перевел",
        "перевела",
        "перевести",

        "выдал",
        "выдала",
        "выдать",

        "дал",
        "дала",
        "дать",

        "отправил",
        "отправила",
        "отправить",

        "перечислил",
        "перечислила",
        "перечислить"
    ]

    is_transfer = any(
        re.search(
            rf"\b{re.escape(word)}\b",
            normalized
        )
        for word in transfer_words
    )

    if is_transfer:

        amount = extract_amount(
            text
        )

        if amount is None:

            await update.message.reply_text(
                "❌ Не смогла определить сумму.\n\n"
                "Напиши, например:\n"
                "скинул Иванову 20000 на материалы\n\n"
                "Или:\n"
                "перевел Иванову 20000 на материалы"
            )

            return

        db = get_db()

        try:

            # ---------------------------------------------
            # ИЩЕМ СОТРУДНИКА
            # ---------------------------------------------

            employee = find_employee_in_text(
                db,
                text
            )

            if not employee:

                await update.message.reply_text(
                    "❌ Не смогла определить сотрудника.\n\n"
                    "Укажите ФИО сотрудника.\n\n"
                    "Например:\n"
                    "Перевел Иванову Ивану 20000 "
                    "на материалы"
                )

                return

            # ---------------------------------------------
            # ФОРМИРУЕМ НАЗНАЧЕНИЕ
            # ---------------------------------------------

            purpose = text

            for phrase in transfer_words:

                purpose = re.sub(
                    rf"\b{re.escape(phrase)}\b",
                    "",
                    purpose,
                    flags=re.IGNORECASE
                )

            # Убираем сумму из назначения
            purpose = re.sub(
                r"(?<!\d)"
                r"\d{1,3}(?:[ \t]\d{3})+"
                r"(?!\d)",
                "",
                purpose
            )

            purpose = re.sub(
                r"(?<!\d)"
                r"\d+(?:[.,]\d+)?"
                r"(?!\d)",
                "",
                purpose
            )

            # Убираем обозначения валюты
            purpose = re.sub(
                r"\bруб(?:\.|лей|ля)?\b",
                "",
                purpose,
                flags=re.IGNORECASE
            )

            purpose = purpose.replace(
                "₽",
                ""
            )

            # Убираем лишние пробелы
            purpose = re.sub(
                r"\s+",
                " ",
                purpose
            ).strip()

            # Если после очистки ничего нет
            if not purpose:
                purpose = "Выдача денежных средств"

            # ---------------------------------------------
            # СОЗДАЁМ MONEY TRANSFER
            # (текстовая команда — счёт по умолчанию Банк)
            # ---------------------------------------------

            transfer = MoneyTransfer(
                telegram_user_id=employee.id,
                amount=amount,
                account="cash",
                purpose=purpose,
                comment=text,
                status="issued",
                created_at=date.today()
            )

            db.add(
                transfer
            )

            db.commit()

            db.refresh(
                transfer
            )

            await update.message.reply_text(
                "✅ Деньги выданы сотруднику\n\n"
                f"👤 Сотрудник: "
                f"{employee.full_name}\n"
                f"🏦 Счёт: 🏦 Банк\n"
                f"💰 Сумма: "
                f"{amount:,.2f} ₽\n"
                f"📝 Назначение: "
                f"{purpose}\n\n"
                "🧾 После покупки сотрудник "
                "должен отправить чек.\n\n"
                "Операция расхода будет создана "
                "после получения чека.",
                reply_markup=director_keyboard()
            )

        except Exception as error:

            db.rollback()

            print(
                "Ошибка создания выдачи:",
                error
            )

            await update.message.reply_text(
                "❌ Не удалось создать выдачу денег.\n\n"
                f"Ошибка: {error}"
            )

        finally:

            db.close()

        return

    # =====================================================
    # ДОХОД
    # =====================================================

    is_income = (
        lower.startswith("+")
        or "пришло" in normalized
        or "поступило" in normalized
        or "получили" in normalized
    )

    if is_income:

        amount = extract_amount(
            text
        )

        if amount is None:

            await update.message.reply_text(
                "❌ Не смогла определить сумму.\n\n"
                "Напиши, например:\n"
                "пришло 500000 от клиента"
            )

            return

        db = get_db()

        try:

            transaction = Transaction(
                type="income",
                category="Доход",
                amount=amount,
                description=text,
                date=date.today()
            )

            db.add(
                transaction
            )

            db.commit()

        except Exception as error:

            db.rollback()

            print(
                "Ошибка записи дохода:",
                error
            )

            await update.message.reply_text(
                "❌ Не удалось записать доход."
            )

            return

        finally:

            db.close()

        await update.message.reply_text(
            "✅ Доход записан\n\n"
            f"💰 Сумма: "
            f"{amount:,.2f} ₽\n"
            f"📝 {text}",
            reply_markup=director_keyboard()
        )

        return

    # =====================================================
    # ОБЫЧНЫЙ РАСХОД ДИРЕКТОРА
    # =====================================================

    is_expense = (
        lower.startswith("-")
        or "купил" in normalized
        or "купила" in normalized
        or "оплатил" in normalized
        or "оплатила" in normalized
        or "заплатил" in normalized
        or "заплатила" in normalized
    )

    if is_expense:

        amount = extract_amount(
            text
        )

        if amount is None:

            await update.message.reply_text(
                "❌ Не смогла определить сумму.\n\n"
                "Напиши, например:\n"
                "купил принтер 35000"
            )

            return

        category = detect_category(
            text
        )

        db = get_db()

        try:

            transaction = Transaction(
                type="expense",
                category=category,
                amount=amount,
                description=text,
                date=date.today()
            )

            db.add(
                transaction
            )

            db.commit()

        except Exception as error:

            db.rollback()

            print(
                "Ошибка записи расхода:",
                error
            )

            await update.message.reply_text(
                "❌ Не удалось записать расход."
            )

            return

        finally:

            db.close()

        await update.message.reply_text(
            "✅ Операция записана\n\n"
            f"💸 Сумма: "
            f"{amount:,.2f} ₽\n"
            f"📂 Категория: "
            f"{category}\n"
            f"📝 {text}",
            reply_markup=director_keyboard()
        )

        return

    # =====================================================
    # НЕИЗВЕСТНАЯ КОМАНДА
    # =====================================================

    await update.message.reply_text(
        "Не поняла команду.\n\n"
        "Примеры:\n\n"
        "💳 Выдать деньги:\n"
        "скинул Иванову 20000 на материалы\n"
        "перевел Иванову 20000 на материалы\n"
        "выдал Иванову 20000 на материалы\n"
        "отправил Иванову 20000 на материалы\n\n"
        "💸 Купить напрямую:\n"
        "купил принтер 35000\n\n"
        "💰 Доход:\n"
        "пришло 500000 от клиента",
        reply_markup=director_keyboard()
    )

# =========================================================
# СОТРУДНИК — ОБЫЧНЫЙ РАСХОД
# =========================================================


# =========================================================
# ПОДТВЕРЖДЕНИЕ СУММЫ ЧЕКА
# =========================================================

async def handle_receipt_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str
):

    confirmation = context.user_data.get(
        "receipt_confirmation"
    )

    if not confirmation:
        return False

    answer = text.lower().strip()
    # Убираем эмодзи с кнопок
    answer_clean = (
        answer
        .replace("✅", "")
        .replace("❌", "")
        .strip()
    )

    yes_words = {
        "да",
        "верно",
        "подтверждаю",
        "правильно",
        "подтвердить",
        "подтверждаю сумму",
        "✅ да",
    }

    no_words = {
        "нет",
        "неверно",
        "неправильно",
        "отмена",
        "отменить",
        "❌ нет",
    }

    # =====================================================
    # ДА
    # =====================================================

    if answer_clean in yes_words or answer in ("✅ да", "✅ ДА"):

        receipt_id = confirmation["receipt_id"]
        receipt_amount = confirmation["receipt_amount"]

        await create_transaction_from_receipt(
            update=update,
            context=context,
            receipt_id=receipt_id,
            amount=receipt_amount,
            force_confirmed=True
        )

        return True

    # =====================================================
    # НЕТ
    # =====================================================

    if answer_clean in no_words or answer in ("❌ нет", "❌ НЕТ"):

        context.user_data.pop("receipt_confirmation", None)

        await update.message.reply_text(
            "❌ Подтверждение отменено.\n\n"
            "Расход пока НЕ записан.\n\n"
            "🧾 Отправьте правильный чек.",
            reply_markup=employee_keyboard()
        )

        return True

    # =====================================================
    # НЕПОНЯТНЫЙ ОТВЕТ
    # =====================================================

    await update.message.reply_text(
        "⚠️ Нажмите кнопку:\n\n"
        "✅ ДА — сумма в чеке верная\n"
        "❌ НЕТ — чек ошибочный",
        reply_markup=yes_no_keyboard()
    )

    return True

# =========================================================
# ОБРАБОТКА СУММЫ НЕРАСПОЗНАННОГО ЧЕКА
# =========================================================

async def handle_waiting_receipt_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str
):

    receipt_id = context.user_data.get(
        "waiting_receipt_amount"
    )

    if not receipt_id:
        return False

    normalized = (
        text
        .replace("₽", "")
        .replace("руб", "")
        .replace("р.", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )

    try:

        amount = float(
            normalized
        )

        if amount <= 0:
            raise ValueError

    except ValueError:

        await update.message.reply_text(
            "❌ Некорректная сумма.\n\n"
            "Введите сумму числом.\n\n"
            "Например:\n"
            "3500\n"
            "или\n"
            "3500.50"
        )

        return True

    # Убираем режим ожидания суммы.
    # Если сумма отличается, функция создания
    # установит режим подтверждения.
    context.user_data.pop(
        "waiting_receipt_amount",
        None
    )

    await update.message.reply_text(
        f"🔎 Сумма чека указана: "
        f"{amount:,.2f} ₽\n\n"
        "Проверяю её с указанным ранее расходом..."
    )

    await create_transaction_from_receipt(
        update=update,
        context=context,
        receipt_id=receipt_id,
        amount=amount
    )

    return True

# =========================================================
# ОСНОВНАЯ ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ
# =========================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.text:
        return

    telegram_user = update.effective_user

    if not telegram_user:
        return

    text = update.message.text.strip()

    if not text:
        return

    # =====================================================
    # РЕГИСТРАЦИЯ ФИО
    # =====================================================

    if context.user_data.get("awaiting_full_name"):

        full_name = normalize_full_name(text)

        if not is_valid_full_name(full_name):

            await update.message.reply_text(
                "❌ Пожалуйста, напишите ФИО полностью.\n\n"
                "Например:\n"
                "Иванов Иван Иванович"
            )

            return

        if len(full_name) > 150:

            await update.message.reply_text(
                "❌ ФИО слишком длинное.\n\n"
                "Введите фамилию, имя и отчество."
            )

            return

        db = get_db()

        try:

            user = (
                db.query(TelegramUser)
                .filter(
                    TelegramUser.telegram_id
                    == telegram_user.id
                )
                .first()
            )

            if not user:

                role = "employee"

                if (
                    DIRECTOR_TELEGRAM_ID
                    and telegram_user.id
                    == DIRECTOR_TELEGRAM_ID
                ):
                    role = "director"

                user = TelegramUser(
                    telegram_id=telegram_user.id,
                    full_name=full_name,
                    role=role,
                    is_active=True
                )

                db.add(user)
                db.commit()
                db.refresh(user)

            else:

                user.full_name = full_name

                db.commit()
                db.refresh(user)

            context.user_data.pop(
                "awaiting_full_name",
                None
            )

        except Exception as error:

            db.rollback()

            print(
                "Ошибка сохранения ФИО:",
                error
            )

            await update.message.reply_text(
                "❌ Не удалось сохранить ФИО.\n\n"
                "Попробуйте ещё раз."
            )

            return

        finally:

            db.close()

        if user.role == "director":

            await update.message.reply_text(
                f"✅ ФИО сохранено:\n"
                f"{user.full_name}\n\n"
                "👑 Вы зарегистрированы как директор."
            )

        else:

            await update.message.reply_text(
                f"✅ ФИО сохранено:\n"
                f"{user.full_name}\n\n"
                "👤 Вы зарегистрированы как сотрудник."
            )

        await show_main_menu(
            update,
            user
        )

        return

    # =====================================================
    # ПОЛУЧАЕМ ПОЛЬЗОВАТЕЛЯ
    # =====================================================

    user = get_or_create_telegram_user(
        telegram_user
    )

    if not user:

        context.user_data[
            "awaiting_full_name"
        ] = True

        await update.message.reply_text(
            "👤 Для начала работы напишите ваше ФИО полностью.\n\n"
            "Например:\n"
            "Иванов Иван Иванович"
        )

        return

    # =====================================================
    # ПОДТВЕРЖДЕНИЕ СУММЫ ЧЕКА
    # =====================================================

    if context.user_data.get(
        "receipt_confirmation"
    ):

        handled = await handle_receipt_confirmation(
            update,
            context,
            text
        )

        if handled:
            return

    # =====================================================
    # ВВОД СУММЫ НЕРАСПОЗНАННОГО ЧЕКА
    # =====================================================

    if context.user_data.get(
        "waiting_receipt_amount"
    ):

        handled = await handle_waiting_receipt_amount(
            update,
            context,
            text
        )

        if handled:
            return

    # =====================================================
    # СОТРУДНИК
    # =====================================================

    if user.role == "employee":

        # -------------------------------------------------
        # СООБЩИТЬ О РАСХОДЕ (подсказка)
        # -------------------------------------------------

        if text == "💸 Сообщить о расходе":
            context.user_data["expense_flow"] = {
                "step": "category"
            }
            await update.message.reply_text(
                "💸 Новый расход\n\n"
                "Выберите категорию:",
                reply_markup=expense_category_keyboard()
            )
            return

        # -------------------------------------------------
        # ШАГИ РАСХОДА КНОПКАМИ (категория → сумма → описание)
        # -------------------------------------------------

        if context.user_data.get("expense_flow"):
            handled = await handle_expense_flow(
                update, context, user, text
            )
            if handled:
                return

        # -------------------------------------------------
        # ОСТАТОК ДЕНЕГ
        # -------------------------------------------------

        if text == "💳 Остаток денег":
            db = get_db()
            try:
                balance = get_employee_balance(db, user.id)
                await update.message.reply_text(
                    f"💳 У вас осталось: {balance:,.2f} ₽\n\n"
                    "Это деньги, которые выдал директор.\n"
                    "После покупки напишите расход и "
                    "отправьте чек — сумма спишется.",
                    reply_markup=employee_keyboard()
                )
            finally:
                db.close()
            return

        # -------------------------------------------------
        # МОИ РАСХОДЫ
        # -------------------------------------------------

        if text == "💰 Мои расходы":

            await my_expenses(
                update,
                user
            )

            return

        # -------------------------------------------------
        # СОЗДАТЬ ЗАПРОС
        # -------------------------------------------------

        if text == "📦 Создать запрос":

            await start_purchase_request(
                update,
                context
            )

            return

        # -------------------------------------------------
        # МОИ ЗАПРОСЫ
        # -------------------------------------------------

        if text == "📋 Мои запросы":

            await my_purchase_requests(
                update,
                context,
                user
            )

            return

        # -------------------------------------------------
        # ОТПРАВИТЬ ЧЕК
        # -------------------------------------------------

        if text == "🧾 Отправить чек":

            pending_expense = context.user_data.get(
                "pending_expense"
            )

            if not pending_expense:
                await update.message.reply_text(
                    "Сначала оформите расход "
                    "(«💸 Сообщить о расходе»),\n"
                    "потом пришлите фото чека.",
                    reply_markup=employee_keyboard()
                )
                return

            await update.message.reply_text(
                "📷 Пришлите фото или файл чека\n"
                "к последнему расходу:\n\n"
                f"💰 {pending_expense['amount']:,.2f} ₽\n"
                f"📂 {pending_expense['category']}\n"
                f"📝 {pending_expense['description']}",
                reply_markup=employee_keyboard()
            )
            return

        # -------------------------------------------------
        # ПРОФИЛЬ
        # -------------------------------------------------

        if text == "👤 Мой профиль":

            await me(
                update,
                context
            )

            return

        # -------------------------------------------------
        # ШАГ СОЗДАНИЯ ЗАЯВКИ
        # -------------------------------------------------

        if context.user_data.get(
            "purchase_request"
        ):

            handled = await handle_purchase_request_step(
                update,
                context,
                user,
                text
            )

            if handled:
                return

        # -------------------------------------------------
        # ОБЫЧНЫЙ РАСХОД
        # -------------------------------------------------

        await handle_employee_text(
            update,
            context,
            text
        )

        return

    # =====================================================
    # ДИРЕКТОР
    # =====================================================

    if user.role == "director":

        # -------------------------------------------------
        # ШАГИ ВЫДАЧИ ДЕНЕГ (выбор сотрудника → сумма)
        # -------------------------------------------------

        if context.user_data.get("money_transfer"):
            handled = await handle_money_transfer_step(
                update,
                context,
                text
            )
            if handled:
                return

        # -------------------------------------------------
        # КНОПКА: ВЫДАТЬ ДЕНЬГИ (выбор из списка сотрудников)
        # -------------------------------------------------

        if text == "💳 Выдать деньги":
            await start_money_transfer(update, context)
            return

        # -------------------------------------------------
        # ЗАЯВКИ
        # -------------------------------------------------

        if text == "📦 Заявки":

            await director_purchase_requests(
                update
            )

            return

        # -------------------------------------------------
        # ИСТОРИЯ ОПЕРАЦИЙ
        # -------------------------------------------------

        if text == "📜 История операций":

            await director_operations_history(
                update,
                context,
                offset=0
            )

            return

        # -------------------------------------------------
        # ВЫДАННЫЕ ДЕНЬГИ
        # -------------------------------------------------

        if text == "💳 Выданные деньги":

            await director_money_transfers(
                update
            )

            return

        # -------------------------------------------------
        # ОТЧЁТЫ
        # -------------------------------------------------

        if text == "📊 Отчёты":

            await summary(
                update,
                context
            )

            return

        # -------------------------------------------------
        # СОТРУДНИКИ
        # -------------------------------------------------

        if text == "👤 Сотрудники":

            await director_employees(
                update
            )

            return

        # -------------------------------------------------
        # ТЕКСТОВАЯ КОМАНДА ДИРЕКТОРА
        # -------------------------------------------------

        await handle_director_text(
            update,
            text
        )


# =========================================================
# КЛАВИАТУРА
# =========================================================

def get_main_keyboard(user):

    if user.role == "director":
        return director_keyboard()

    return employee_keyboard()


# =========================================================
# ПОКАЗ ГЛАВНОГО МЕНЮ
# =========================================================

async def show_main_menu(
    update: Update,
    user
):

    if user.role == "director":

        text = (
            "👑 Меню директора\n\n"
            "Выберите нужный раздел:"
        )

    else:

        text = (
            "👤 Меню сотрудника\n\n"
            "Выберите нужный раздел:"
        )

    await update.message.reply_text(
        text,
        reply_markup=get_main_keyboard(user)
    )


# =========================================================
# СРАЗУ ЗАПИСАТЬ РАСХОД (списание без ожидания чека)
# =========================================================

def record_employee_expense_now(
    db,
    telegram_user,
    user,
    amount: float,
    category: str,
    description: str,
    transfer_id=None,
):
    """
    Создаёт Transaction + EmployeeExpense сразу.
    Если есть transfer_id — создаёт «заглушку» Receipt,
    чтобы остаток по выдаче тоже уменьшился.
    Чек сотрудник может прислать позже (файл подставится).
    """
    amount = float(amount)

    transaction = Transaction(
        type="expense",
        category=category,
        amount=amount,
        description=description,
        date=date.today(),
    )
    db.add(transaction)
    db.flush()

    employee_expense = EmployeeExpense(
        telegram_user_id=telegram_user.id,
        transaction_id=transaction.id,
        description=description,
    )
    db.add(employee_expense)

    receipt = None
    if transfer_id:
        receipt = Receipt(
            transaction_id=transaction.id,
            telegram_user_id=telegram_user.id,
            transfer_id=transfer_id,
            filename="ожидается_чек",
            filepath="",
            ocr_text=None,
            shop_name=None,
            receipt_amount=amount,
            receipt_date=date.today(),
            uploaded_at=date.today(),
        )
        db.add(receipt)

        transfer = (
            db.query(MoneyTransfer)
            .filter(MoneyTransfer.id == transfer_id)
            .first()
        )
        if transfer:
            update_transfer_status(db, transfer)

    db.commit()

    if receipt:
        db.refresh(receipt)
    db.refresh(transaction)

    return transaction, receipt


# =========================================================
# РАСХОД КНОПКАМИ: категория → сумма → описание
# =========================================================

async def handle_expense_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user,
    text: str
):
    """
    Пошаговый расход через кнопки:
    1) категория
    2) сумма
    3) описание (можно пропустить)
    → затем ждём чек
    """
    data = context.user_data.get("expense_flow")
    if not data:
        return False

    step = data.get("step")

    # ---------- ОТМЕНА ----------
    if text == "❌ Отмена":
        context.user_data.pop("expense_flow", None)
        await update.message.reply_text(
            "❌ Расход отменён.",
            reply_markup=employee_keyboard()
        )
        return True

    # ---------- ШАГ 1: КАТЕГОРИЯ ----------
    if step == "category":
        category = EXPENSE_CATEGORY_MAP.get(text)
        if not category:
            await update.message.reply_text(
                "Выберите категорию кнопкой ниже:",
                reply_markup=expense_category_keyboard()
            )
            return True

        data["category"] = category
        data["step"] = "amount"

        await update.message.reply_text(
            f"📂 Категория: {category}\n\n"
            "Введите сумму — можно сразу с описанием:\n\n"
            "150 на такси\n"
            "или просто:\n"
            "3500",
            reply_markup=expense_cancel_keyboard()
        )
        return True

    # ---------- ШАГ 2: СУММА (можно сразу «150 на такси») ----------
    if step == "amount":
        amount = extract_amount(text)
        if amount is None or amount <= 0:
            await update.message.reply_text(
                "❌ Некорректная сумма.\n\n"
                "Введите число, например:\n"
                "3500\n"
                "или сразу:\n"
                "150 на такси",
                reply_markup=expense_cancel_keyboard()
            )
            return True

        # Описание из того же сообщения: «150 на такси» → «на такси»
        desc_from_amount = text
        desc_from_amount = re.sub(
            r"(?<!\d)\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?",
            " ",
            desc_from_amount,
        )
        desc_from_amount = re.sub(
            r"\b(руб(?:\.|лей|ля|ль)?|р\.?|₽)\b",
            " ",
            desc_from_amount,
            flags=re.IGNORECASE,
        )
        desc_from_amount = re.sub(
            r"\s+", " ", desc_from_amount
        ).strip(" ,.-")

        db = get_db()
        try:
            balance = get_employee_balance(db, user.id)
            if amount > balance + 0.01:
                await update.message.reply_text(
                    "❌ Недостаточно средств.\n\n"
                    f"💳 Осталось: {balance:,.2f} ₽\n"
                    f"💸 Нужно: {amount:,.2f} ₽",
                    reply_markup=expense_cancel_keyboard()
                )
                return True

            active = get_active_money_transfer(
                db, update.effective_user.id
            )
            data["transfer_id"] = (
                active.id if active else None
            )
            data["balance"] = balance
        finally:
            db.close()

        data["amount"] = float(amount)

        # Если в сообщении уже есть «на что» — сразу записываем
        if desc_from_amount:
            data["description"] = desc_from_amount
            await _finish_expense_flow(
                update, context, user, data
            )
            return True

        # Иначе спрашиваем описание отдельно
        data["step"] = "description"
        await update.message.reply_text(
            f"💰 Сумма: {amount:,.2f} ₽\n\n"
            "Кратко напишите, на что потратили\n"
            "(или «⏭ Без описания»):",
            reply_markup=expense_description_keyboard()
        )
        return True

    # ---------- ШАГ 3: ОПИСАНИЕ ----------
    if step == "description":
        if text == "⏭ Без описания":
            description = data["category"]
        else:
            description = text.strip() or data["category"]

        data["description"] = description
        await _finish_expense_flow(
            update, context, user, data
        )
        return True

    return False


async def _finish_expense_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user,
    data: dict,
):
    """Записывает расход и отвечает пользователю."""
    amount = data["amount"]
    category = data["category"]
    description = data.get("description") or category
    transfer_id = data.get("transfer_id")

    db = get_db()
    try:
        transaction, receipt = record_employee_expense_now(
            db=db,
            telegram_user=update.effective_user,
            user=user,
            amount=amount,
            category=category,
            description=description,
            transfer_id=transfer_id,
        )
        new_balance = get_employee_balance(db, user.id)
    except Exception as error:
        db.rollback()
        print("Ошибка записи расхода:", error)
        await update.message.reply_text(
            "❌ Не удалось записать расход.",
            reply_markup=employee_keyboard()
        )
        context.user_data.pop("expense_flow", None)
        return
    finally:
        db.close()

    context.user_data["pending_expense"] = {
        "amount": amount,
        "description": description,
        "category": category,
        "transfer_id": transfer_id,
        "transaction_id": transaction.id,
        "receipt_id": receipt.id if receipt else None,
    }
    context.user_data.pop("expense_flow", None)

    await update.message.reply_text(
        "✅ Расход записан!\n\n"
        f"💰 Списано: {amount:,.2f} ₽\n"
        f"📂 Категория: {category}\n"
        f"📝 На что: {description}\n"
        f"💳 Осталось: {new_balance:,.2f} ₽\n\n"
        "Операция уже есть на сайте.\n"
        "📷 Можете прислать фото чека для отчёта.",
        reply_markup=employee_keyboard()
    )

    if DIRECTOR_TELEGRAM_ID:
        try:
            await context.bot.send_message(
                chat_id=DIRECTOR_TELEGRAM_ID,
                text=(
                    "💸 РАСХОД СОТРУДНИКА\n\n"
                    f"👤 {user.full_name}\n"
                    f"💰 {amount:,.2f} ₽\n"
                    f"📂 {category}\n"
                    f"📝 {description}\n"
                    f"💳 Остаток: {new_balance:,.2f} ₽"
                ),
            )
        except Exception as error:
            print("Не удалось уведомить директора:", error)


# =========================================================
# СОТРУДНИК — ОБЫЧНЫЙ РАСХОД (деньги с баланса / активной выдачи)
# =========================================================

async def handle_employee_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str
):
    """
    Сотрудник пишет сумму и назначение (например: «Потратил 3500 на бензин»).
    Деньги берутся с общего баланса (выданные директором суммы).
    """
    telegram_user = update.effective_user

    if not telegram_user or not update.message:
        return

    user = get_or_create_telegram_user(telegram_user)

    if not user:
        return

    # Игнорируем нажатия кнопок меню (они обрабатываются выше)
    menu_buttons = {
        "💸 Сообщить о расходе",
        "🧾 Отправить чек",
        "💰 Мои расходы",
        "💳 Остаток денег",
        "📦 Создать запрос",
        "📋 Мои запросы",
        "👤 Мой профиль",
    }
    if text in menu_buttons:
        return

    amount = extract_amount(text)

    if amount is None or amount <= 0:
        await update.message.reply_text(
            "❌ Не удалось определить сумму расхода.\n\n"
            "Напишите, например:\n"
            "Потратил 3500 на бензин\n\n"
            "Или нажмите кнопку «💸 Сообщить о расходе».",
            reply_markup=employee_keyboard()
        )
        return

    db = get_db()

    try:
        current_balance = get_employee_balance(db, user.id)

        if amount > current_balance + 0.01:
            await update.message.reply_text(
                "❌ Недостаточно средств.\n\n"
                f"💳 Осталось: {current_balance:,.2f} ₽\n"
                f"💸 Нужно: {amount:,.2f} ₽\n\n"
                "Попросите директора выдать деньги.",
                reply_markup=employee_keyboard()
            )
            return

        active_transfer = get_active_money_transfer(
            db, telegram_user.id
        )
        transfer_id = (
            active_transfer.id if active_transfer else None
        )
        category = detect_category(text)

        transaction, receipt = record_employee_expense_now(
            db=db,
            telegram_user=telegram_user,
            user=user,
            amount=amount,
            category=category,
            description=text,
            transfer_id=transfer_id,
        )
        new_balance = get_employee_balance(db, user.id)

        context.user_data["pending_expense"] = {
            "amount": float(amount),
            "description": text,
            "category": category,
            "transfer_id": transfer_id,
            "transaction_id": transaction.id,
            "receipt_id": receipt.id if receipt else None,
        }

        await update.message.reply_text(
            "✅ Расход записан!\n\n"
            f"💰 Списано: {amount:,.2f} ₽\n"
            f"📂 Категория: {category}\n"
            f"📝 На что: {text}\n"
            f"💳 Осталось: {new_balance:,.2f} ₽\n\n"
            "Операция уже есть на сайте.\n"
            "📷 Можете прислать фото чека для отчёта.",
            reply_markup=employee_keyboard()
        )

        if DIRECTOR_TELEGRAM_ID:
            try:
                await context.bot.send_message(
                    chat_id=DIRECTOR_TELEGRAM_ID,
                    text=(
                        "💸 РАСХОД СОТРУДНИКА\n\n"
                        f"👤 {user.full_name}\n"
                        f"💰 {amount:,.2f} ₽\n"
                        f"📂 {category}\n"
                        f"📝 {text}\n"
                        f"💳 Остаток: {new_balance:,.2f} ₽"
                    ),
                )
            except Exception as error:
                print("Не удалось уведомить директора:", error)

    except Exception as error:
        db.rollback()
        print("Ошибка записи расхода:", error)
        await update.message.reply_text(
            "❌ Не удалось записать расход.",
            reply_markup=employee_keyboard()
        )
    finally:
        db.close()


# =========================================================
# СВОДКА
# =========================================================

async def summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    telegram_user = update.effective_user

    if not telegram_user:
        return

    user = get_or_create_telegram_user(
        telegram_user
    )

    if not user:
        return

    if user.role != "director":

        await update.message.reply_text(
            "⛔ Эта команда доступна только директору."
        )

        return

    db = get_db()

    try:

        transactions = (
            db.query(Transaction)
            .all()
        )

        income = sum(
            float(t.amount)
            for t in transactions
            if t.type == "income"
        )

        expense = sum(
            float(t.amount)
            for t in transactions
            if t.type == "expense"
        )

        balance = (
            income - expense
        )

    except Exception as error:

        print(
            "Ошибка формирования сводки:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось получить сводку."
        )

        return

    finally:

        db.close()

    await update.message.reply_text(
        "📊 Финансовая сводка\n\n"
        f"💰 Доходы: "
        f"{income:,.2f} ₽\n"
        f"💸 Расходы: "
        f"{expense:,.2f} ₽\n"
        f"💵 Баланс: "
        f"{balance:,.2f} ₽",
        reply_markup=director_keyboard()
    )


# =========================================================
# ПРОВЕРКА ПОДПИСОК
# =========================================================

async def check_subscription_notifications(
    context: ContextTypes.DEFAULT_TYPE
):

    db = get_db()

    try:

        from ..models import RecurringTransaction

        today = date.today()

        recurring = (
            db.query(RecurringTransaction)
            .filter(
                RecurringTransaction.is_active
                == True
            )
            .all()
        )

        for item in recurring:

            if not item.next_payment_date:
                continue

            days_left = (
                item.next_payment_date
                - today
            ).days

            if days_left == 3:

                await send_subscription_notification(
                    context.bot,
                    "Скоро списание",
                    (
                        f"🔄 Подписка: {item.name}\n"
                        f"💰 Сумма: "
                        f"{item.amount:,.2f} ₽\n"
                        f"📅 Списание: "
                        f"{item.next_payment_date.strftime('%d.%m.%Y')}\n\n"
                        "До списания осталось 3 дня."
                    )
                )

            elif days_left == 1:

                await send_subscription_notification(
                    context.bot,
                    "Завтра списание",
                    (
                        f"🔄 Подписка: {item.name}\n"
                        f"💰 Сумма: "
                        f"{item.amount:,.2f} ₽\n"
                        "📅 Списание: завтра\n\n"
                        "Подготовьте средства для списания."
                    )
                )

            elif days_left == 0:

                await send_subscription_notification(
                    context.bot,
                    "Сегодня списание",
                    (
                        f"🔄 Подписка: {item.name}\n"
                        f"💰 Сумма: "
                        f"{item.amount:,.2f} ₽\n"
                        "📅 Дата списания: сегодня\n\n"
                        "Сегодня запланировано списание."
                    )
                )

    except Exception as error:

        print(
            "❌ Ошибка проверки подписок:",
            error
        )

    finally:

        db.close()


# =========================================================
# ЗАПУСК
# =========================================================

def main():

    request = HTTPXRequest(
        proxy=TELEGRAM_PROXY,
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
    )

    get_updates_request = HTTPXRequest(
        proxy=TELEGRAM_PROXY,
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
    )

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )

    # =====================================================
    # ПРОВЕРКА ПОДПИСОК
    # =====================================================

    application.job_queue.run_daily(
        check_subscription_notifications,
        time=datetime.time(
            hour=9,
            minute=0
        )
    )

    # =====================================================
    # ОШИБКИ
    # =====================================================

    application.add_error_handler(
        error_handler
    )

    # =====================================================
    # КОМАНДЫ
    # =====================================================

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "id",
            my_id
        )
    )

    application.add_handler(
        CommandHandler(
            "me",
            me
        )
    )

    application.add_handler(
        CommandHandler(
            "summary",
            summary
        )
    )

    # =====================================================
    # CALLBACK (кнопки заявок + «Ещё» в истории)
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            handle_purchase_request_callback,
            pattern=r"^pr_(approve|reject|done|notdone):"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_ops_more_callback,
            pattern=r"^ops_more:"
        )
    )

    # =====================================================
    # ЧЕКИ
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.PHOTO
            | filters.Document.ALL,
            handle_receipt
        )
    )

    # =====================================================
    # ТЕКСТ
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    print(
        "🤖 Finance Telegram Bot запущен..."
    )

    application.run_polling()


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()