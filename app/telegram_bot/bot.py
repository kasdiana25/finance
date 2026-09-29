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
    KeyboardButton
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

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
                KeyboardButton("💰 Финансы")
            ],
            [
                KeyboardButton("📦 Заявки"),
                KeyboardButton("🧾 Чеки")
            ],
            [
                KeyboardButton("💳 Выданные деньги"),
                KeyboardButton("📊 Отчёты")
            ],
            [
                KeyboardButton("👤 Сотрудники")
            ],
            [
                KeyboardButton("🏠 Главное меню")
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

            # Переходим к вводу суммы
            data["step"] = "amount"

            # Получаем текущий баланс
            balance = get_employee_balance(
                db,
                employee.id
            )

            await update.message.reply_text(
                f"👤 Сотрудник: {employee.full_name}\n\n"
                f"💰 Текущий баланс: "
                f"{balance:,.2f} ₽\n\n"
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

        finally:
            db.close()

        return True

    # =====================================================
    # ШАГ 2. ВВОД СУММЫ
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

                # Назначение теперь общее.
                # Деньги НЕ привязаны к конкретной покупке.
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

async def my_purchase_requests(
    update: Update,
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
                "📋 У вас пока нет заявок."
            )

            return

        status_names = {
            "new": "🟡 Новая",
            "approved": "🟢 Одобрена",
            "ordered": "🔵 Заказана",
            "received": "📦 Получена",
            "rejected": "🔴 Отклонена"
        }

        lines = [
            "📋 Ваши заявки:\n"
        ]

        for request in requests:

            status = status_names.get(
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

            lines.append(
                f"📌 {request.number}\n"
                f"Название: {request.title}\n"
                f"Статус: {status}\n"
                f"Дата: {created_text}\n"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    finally:

        db.close()


# =========================================================
# СОЗДАНИЕ ЗАЯВКИ
# =========================================================

async def start_purchase_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    context.user_data[
        "purchase_request"
    ] = {
        "step": "product",
        "items": []
    }

    await update.message.reply_text(
        "📦 Создание заявки\n\n"
        "Напишите одним сообщением:\n"
        "товар, количество и цену за 1 шт.\n\n"
        "Пример:\n"
        "Монитор Samsung, 2 шт, 25000"
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

    if data.get("step") != "product":
        return False

    if not text.strip():
        await update.message.reply_text(
            "❌ Напишите товар, количество и цену.\n\n"
            "Пример: Монитор Samsung, 2 шт, 25000"
        )
        return True

    # Количество: 2 шт / 2 штук / 2 ед.
    quantity_match = re.search(
        r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:шт\.?|штук|ед\.?)",
        text,
        flags=re.IGNORECASE
    )

    if not quantity_match:
        await update.message.reply_text(
            "❌ Не понял количество.\n\n"
            "Пример: Монитор Samsung, 2 шт, 25000"
        )
        return True

    quantity = float(
        quantity_match.group(1).replace(",", ".")
    )
    if quantity <= 0:
        await update.message.reply_text(
            "❌ Количество должно быть больше нуля."
        )
        return True

    # Убираем количество, чтобы найти цену
    text_wo_qty = re.sub(
        r",?\s*\d+(?:[.,]\d+)?\s*(?:шт\.?|штук|ед\.?)",
        " ",
        text,
        flags=re.IGNORECASE
    )

    # Цена: 25000 / 25 000 / 25000₽ / 25000 руб / 25000 р / 25000.50
    estimated_price = extract_amount(text_wo_qty)

    if estimated_price is None or estimated_price <= 0:
        await update.message.reply_text(
            "❌ Не понял цену.\n\n"
            "Можно писать: 25000, 25000₽, 25000 руб, 25 000 р"
        )
        return True

    # Название = всё без количества и без цены/валюты
    product_name = re.sub(
        r",?\s*\d+(?:[.,]\d+)?\s*(?:шт\.?|штук|ед\.?)",
        "",
        text,
        flags=re.IGNORECASE
    )
    product_name = re.sub(
        r"(?<!\d)\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?"
        r"\s*(?:₽|руб(?:\.|лей|ля|ль)?|р\.?)?",
        "",
        product_name,
        flags=re.IGNORECASE
    )
    product_name = re.sub(r"\s+", " ", product_name).strip(" ,.-")

    if not product_name:
        await update.message.reply_text(
            "❌ Не понял название товара.\n\n"
            "Пример: Монитор Samsung, 2 шт, 25000"
        )
        return True

    # Подготавливаем данные для сохранения.
    data["product_name"] = product_name
    data["title"] = product_name
    data["description"] = None
    data["quantity"] = quantity
    data["estimated_price"] = estimated_price

    # Внутренние значения для совместимости с существующей моделью.
    # Пользователь категорию и приоритет не выбирает.
    data["category"] = "Без категории"
    data["priority"] = "normal"

    total_price = quantity * estimated_price

    await update.message.reply_text(
        "✅ Заявка принята!\n\n"
        f"🛒 Товар: {product_name}\n"
        f"🔢 Количество: {quantity:g} шт.\n"
        f"💰 Цена за 1 шт.: {estimated_price:,.2f} ₽\n"
        f"💵 Общая сумма: {total_price:,.2f} ₽\n\n"
        "⏳ Сохраняю заявку..."
    )

    await save_purchase_request(update, context, user)

    return True


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

    db = get_db()

    try:

        purchase_request = PurchaseRequest(
            number="TEMP",
            telegram_user_id=user.id,
            title=data["title"],
            description=data.get(
                "description"
            ),
            category=data.get(
                "category"
            ),
            priority=data.get(
                "priority",
                "normal"
            ),
            status="new"
        )

        db.add(
            purchase_request
        )

        db.flush()

        purchase_request.number = (
            f"З-{purchase_request.id:06d}"
        )

        quantity = float(
            data["quantity"]
        )

        estimated_price = float(
            data["estimated_price"]
        )

        total_price = (
            quantity * estimated_price
        )

        item = PurchaseRequestItem(
            request_id=purchase_request.id,
            product_name=data["product_name"],
            quantity=quantity,
            unit="шт.",
            estimated_price=estimated_price
        )

        db.add(item)

        db.commit()

        db.refresh(
            purchase_request
        )

        priority_names = {
            "low": "Низкий",
            "normal": "Обычный",
            "high": "Высокий",
            "urgent": "Срочный"
        }

        # =================================================
        # ОТВЕТ СОТРУДНИКУ
        # =================================================

        await update.message.reply_text(
            "✅ Заявка создана!\n\n"
            f"📌 Номер: {purchase_request.number}\n"
            f"🛒 Товар: {data['product_name']}\n"
            f"🔢 Количество: {quantity:g} шт.\n"
            f"💰 Цена за 1 шт.: {estimated_price:,.2f} ₽\n"
            f"💵 Общая сумма: {total_price:,.2f} ₽\n\n"
            "Заявка отправлена директору."
        )

        # =================================================
        # УВЕДОМЛЕНИЕ ДИРЕКТОРУ
        # =================================================

        if DIRECTOR_TELEGRAM_ID:

            notification_text = (
                "📦 НОВАЯ ЗАЯВКА\n\n"
                f"📌 Номер: {purchase_request.number}\n"
                f"👤 Сотрудник: {user.full_name}\n"
                f"🛒 Товар: {data['product_name']}\n"
                f"🔢 Количество: {quantity:g} шт.\n"
                f"💰 Цена за 1 шт.: {estimated_price:,.2f} ₽\n"
                f"💵 Общая сумма: {total_price:,.2f} ₽"
            )
            try:

                await context.bot.send_message(
                    chat_id=DIRECTOR_TELEGRAM_ID,
                    text=notification_text
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
            f"Ошибка: {error}"
        )

    finally:

        db.close()

        context.user_data.pop(
            "purchase_request",
            None
        )

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
                "📦 Новых заявок пока нет."
            )

            return

        status_names = {
            "new": "🟡 Новая",
            "approved": "🟢 Одобрена",
            "ordered": "🔵 Заказана",
            "received": "📦 Получена",
            "rejected": "🔴 Отклонена"
        }

        lines = [
            "📦 Последние заявки:\n"
        ]

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

            status = status_names.get(
                request.status,
                request.status
            )

            lines.append(
                f"📌 {request.number}\n"
                f"👤 {employee_name}\n"
                f"📦 {request.title}\n"
                f"{status}\n"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    finally:

        db.close()


# =========================================================
# ФИНАНСЫ
# =========================================================

async def director_finances(
    update: Update
):

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

        balance = income - expense

        await update.message.reply_text(
            "💰 Финансы\n\n"
            f"Доходы: {income:,.2f} ₽\n"
            f"Расходы: {expense:,.2f} ₽\n"
            f"Баланс: {balance:,.2f} ₽"
        )

    finally:

        db.close()


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
                "💳 Выданных денег пока нет."
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

            lines.append(
                f"👤 {employee_name}\n"
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
            "\n".join(lines)
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
            balance = get_employee_balance(db, employee.id)
            lines.append(
                f"• {employee.full_name}\n"
                f"  ID: {employee.telegram_id}\n"
                f"  💳 Баланс: {balance:,.2f} ₽"
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
            # ---------------------------------------------

            transfer = MoneyTransfer(
                telegram_user_id=employee.id,
                amount=amount,
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
                f"💰 Сумма: "
                f"{amount:,.2f} ₽\n"
                f"📝 Назначение: "
                f"{purpose}\n\n"
                "🧾 После покупки сотрудник "
                "должен отправить чек.\n\n"
                "Операция расхода будет создана "
                "после получения чека."
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
            f"📝 {text}"
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
            f"📝 {text}"
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
        "пришло 500000 от клиента"
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
# СОЗДАНИЕ ЗАЯВКИ / ТЕКСТОВЫЕ СООБЩЕНИЯ
# =========================================================

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
    # ГЛАВНОЕ МЕНЮ
    # =====================================================

    if text == "🏠 Главное меню":

        context.user_data.pop("purchase_request", None)
        context.user_data.pop("waiting_receipt_amount", None)
        context.user_data.pop("expense_flow", None)
        context.user_data.pop("pending_expense", None)
        context.user_data.pop("receipt_confirmation", None)
        context.user_data.pop("money_transfer", None)

        await show_main_menu(
            update,
            user
        )

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
        # ФИНАНСЫ
        # -------------------------------------------------

        if text == "💰 Финансы":

            await director_finances(
                update
            )

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
        # ЧЕКИ
        # -------------------------------------------------

        if text == "🧾 Чеки":

            db = get_db()

            try:

                receipts = (
                    db.query(Receipt)
                    .order_by(
                        Receipt.id.desc()
                    )
                    .limit(10)
                    .all()
                )

                if not receipts:

                    await update.message.reply_text(
                        "🧾 Чеков пока нет."
                    )

                    return

                lines = [
                    "🧾 Последние чеки:\n"
                ]

                for receipt in receipts:

                    employee = (
                        db.query(TelegramUser)
                        .filter(
                            TelegramUser.telegram_id
                            == receipt.telegram_user_id
                        )
                        .first()
                    )

                    employee_name = (
                        employee.full_name
                        if employee
                        else "Неизвестный сотрудник"
                    )

                    amount_text = (
                        f"{receipt.receipt_amount:,.2f} ₽"
                        if receipt.receipt_amount
                        is not None
                        else "Сумма не указана"
                    )

                    lines.append(
                        f"🧾 #{receipt.id}\n"
                        f"👤 {employee_name}\n"
                        f"🏪 {receipt.shop_name or 'Не определён'}\n"
                        f"💰 {amount_text}\n"
                        f"📄 {receipt.filename}\n"
                    )

                await update.message.reply_text(
                    "\n".join(lines)
                )

            finally:

                db.close()

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
            "👑 Главное меню директора\n\n"
            "Выберите нужный раздел:"
        )

    else:

        text = (
            "👤 Главное меню сотрудника\n\n"
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
            "Введите сумму расхода:\n\n"
            "Например:\n"
            "3500\n"
            "или\n"
            "3500.50",
            reply_markup=expense_cancel_keyboard()
        )
        return True

    # ---------- ШАГ 2: СУММА ----------
    if step == "amount":
        amount = extract_amount(text)
        if amount is None or amount <= 0:
            await update.message.reply_text(
                "❌ Некорректная сумма.\n\n"
                "Введите число, например:\n"
                "3500",
                reply_markup=expense_cancel_keyboard()
            )
            return True

        db = get_db()
        try:
            balance = get_employee_balance(db, user.id)
            if amount > balance + 0.01:
                await update.message.reply_text(
                    "❌ Недостаточно средств на балансе.\n\n"
                    f"💳 Баланс: {balance:,.2f} ₽\n"
                    f"💸 Расход: {amount:,.2f} ₽\n\n"
                    "Попросите директора пополнить баланс "
                    "или введите меньшую сумму.",
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
        data["step"] = "description"

        await update.message.reply_text(
            f"💰 Сумма: {amount:,.2f} ₽\n\n"
            "Кратко напишите, на что потратили\n"
            "(или нажмите «⏭ Без описания»):",
            reply_markup=expense_description_keyboard()
        )
        return True

    # ---------- ШАГ 3: ОПИСАНИЕ ----------
    if step == "description":
        if text == "⏭ Без описания":
            description = data["category"]
        else:
            description = text.strip() or data["category"]

        amount = data["amount"]
        category = data["category"]
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
            return True
        finally:
            db.close()

        # Чек можно прислать позже — привяжем к уже созданному расходу
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
            f"✅ Расход записан, списано {amount:,.2f} ₽\n\n"
            f"📂 {category}\n"
            f"📝 {description}\n"
            f"💳 Осталось: {new_balance:,.2f} ₽\n\n"
            "📷 Желательно отправить фото чека "
            "(для отчёта).",
            reply_markup=employee_keyboard()
        )

        # Уведомление директору
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

        return True

    return False


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
    Transaction создаётся только после подтверждения чеком.
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
        "🏠 Главное меню",
    }
    if text in menu_buttons:
        return

    amount = extract_amount(text)

    if amount is None or amount <= 0:
        await update.message.reply_text(
            "❌ Не удалось определить сумму расхода.\n\n"
            "Напишите, например:\n"
            "Потратил 3500 на бензин\n\n"
            "Или нажмите кнопку «💸 Сообщить о расходе»."
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
            f"✅ Расход записан, списано {amount:,.2f} ₽\n\n"
            f"📂 {category}\n"
            f"📝 {text}\n"
            f"💳 Осталось: {new_balance:,.2f} ₽\n\n"
            "📷 Желательно отправить фото чека "
            "(для отчёта).",
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
        f"{balance:,.2f} ₽"
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

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
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