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
            MoneyTransfer.status == "issued"
        )
        .order_by(
            MoneyTransfer.id.desc()
        )
        .all()
    )

    for transfer in transfers:

        spent = (
            db.query(Receipt)
            .filter(
                Receipt.transfer_id == transfer.id,
                Receipt.receipt_amount.isnot(None)
            )
            .all()
        )

        spent_amount = sum(
            float(receipt.receipt_amount)
            for receipt in spent
        )

        if spent_amount < float(transfer.amount):
            return transfer

    return None


# =========================================================
# РАСХОД ПО ВЫДАЧЕ
# =========================================================

def get_transfer_spent_amount(
    db,
    transfer_id: int
):
    receipts = (
        db.query(Receipt)
        .filter(
            Receipt.transfer_id == transfer_id,
            Receipt.receipt_amount.isnot(None)
        )
        .all()
    )

    return sum(
        float(receipt.receipt_amount)
        for receipt in receipts
    )


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
# МЕНЮ СОТРУДНИКА
# =========================================================

def employee_keyboard():

    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("💰 Мои расходы"),
                KeyboardButton("📦 Создать запрос")
            ],
            [
                KeyboardButton("📋 Мои запросы"),
                KeyboardButton("🧾 Отправить чек")
            ],
            [
                KeyboardButton("👤 Мой профиль")
            ]
        ],
        resize_keyboard=True
    )


# =========================================================
# МЕНЮ ДИРЕКТОРА
# =========================================================

def director_keyboard():

    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("💰 Финансы"),
                KeyboardButton("📦 Заявки")
            ],
            [
                KeyboardButton("🧾 Чеки"),
                KeyboardButton("💳 Выданные деньги")
            ],
            [
                KeyboardButton("📊 Отчёты"),
                KeyboardButton("👤 Сотрудники")
            ],
            [
                KeyboardButton("🏠 Главное меню")
            ]
        ],
        resize_keyboard=True
    )


# =========================================================
# ОСНОВНОЕ МЕНЮ
# =========================================================

def get_main_keyboard(user):

    if user.role == "director":
        return director_keyboard()

    return employee_keyboard()


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

    if not text:
        return None

    normalized = text.lower()

    normalized = normalized.replace(
        "₽",
        " руб "
    )

    normalized = normalized.replace(
        "р.",
        " руб "
    )

    normalized = normalized.replace(
        "р ",
        " руб "
    )

    lines = [
        line.strip()
        for line in normalized.splitlines()
        if line.strip()
    ]

    total_words = (
        "итог",
        "итого",
        "всего",
        "к оплате",
        "оплате",
        "сумма"
    )

    for i, line in enumerate(lines):

        if not any(
            word in line
            for word in total_words
        ):
            continue

        decimal_matches = re.findall(
            r"(?<!\d)"
            r"\d+(?:[ \t]\d{3})*"
            r"[.,]\d{1,2}"
            r"(?!\d)",
            line
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

        if i + 1 < len(lines):

            next_line = lines[i + 1]

            decimal_matches = re.findall(
                r"(?<!\d)"
                r"\d+(?:[ \t]\d{3})*"
                r"[.,]\d{1,2}"
                r"(?!\d)",
                next_line
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

        integer_matches = re.findall(
            r"(?<!\d)\d+(?!\d)",
            line
        )

        if integer_matches:

            try:

                amount = float(
                    integer_matches[-1]
                )

                if amount > 0:
                    return amount

            except ValueError:
                pass

    for line in lines:

        if (
            "руб" not in line
            and "₽" not in line
        ):
            continue

        matches = re.findall(
            r"(?<!\d)"
            r"\d+(?:[ \t]\d{3})*"
            r"[.,]?\d{0,2}"
            r"(?!\d)",
            line
        )

        for value in reversed(matches):

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

    return None


# =========================================================
# ПАРСИНГ ДАТЫ ЧЕКА
# =========================================================

def parse_receipt_date(value):

    if not value:
        return date.today()

    if isinstance(value, date):
        return value

    if isinstance(value, datetime.datetime):
        return value.date()

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

    context.user_data.pop(
        "purchase_request",
        None
    )

    context.user_data.pop(
        "waiting_receipt_amount",
        None
    )

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
    # СОХРАНЯЕМ RECEIPT СРАЗУ
    # =====================================================

    db = get_db()

    receipt_id = None

    try:

        # Ищем активную выдачу
        active_transfer = (
            get_active_money_transfer(
                db,
                telegram_user.id
            )
        )

        receipt = Receipt(
            transaction_id=None,
            telegram_user_id=telegram_user.id,
            transfer_id=(
                active_transfer.id
                if active_transfer
                else None
            ),
            filename=filename,
            filepath=str(filepath),
            ocr_text=ocr_text,
            shop_name=shop_name,
            receipt_amount=(
                float(amount)
                if amount is not None
                else None
            ),
            receipt_date=receipt_date,
            uploaded_at=date.today()
        )

        db.add(receipt)

        db.commit()

        db.refresh(receipt)

        receipt_id = receipt.id

    except Exception as error:

        db.rollback()

        print(
            "Ошибка сохранения Receipt:",
            error
        )

        await update.message.reply_text(
            "❌ Файл сохранён на диске, "
            "но не удалось создать запись "
            "о чеке в базе данных."
        )

        return

    finally:

        db.close()

    # =====================================================
    # СУММА НЕ НАЙДЕНА
    # =====================================================

    if amount is None:

        context.user_data[
            "waiting_receipt_amount"
        ] = receipt_id

        await update.message.reply_text(
            "✅ Чек сохранён.\n\n"
            f"🧾 Номер чека: #{receipt_id}\n"
            f"📄 Файл: {filename}\n\n"
            "Я не смогла определить сумму автоматически.\n"
            "Введите сумму чека вручную.\n\n"
            "Например:\n"
            "3500"
        )

        return

    # =====================================================
    # СОЗДАЁМ ОПЕРАЦИЮ
    # =====================================================

    await create_transaction_from_receipt(
        update=update,
        context=context,
        receipt_id=receipt_id,
        amount=float(amount)
    )


# =========================================================
# СОЗДАНИЕ ОПЕРАЦИИ ПО ЧЕКУ
# =========================================================

async def create_transaction_from_receipt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    receipt_id: int,
    amount: float
):

    telegram_user = update.effective_user

    if not telegram_user:
        return

    db = get_db()

    try:

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

            return

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

            return

        # =================================================
        # ПРОВЕРЯЕМ ВЫДАЧУ
        # =================================================

        transfer = None

        if receipt.transfer_id:

            transfer = (
                db.query(MoneyTransfer)
                .filter(
                    MoneyTransfer.id
                    == receipt.transfer_id
                )
                .first()
            )

        # =================================================
        # Если выдача есть
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

            if amount > remaining + 0.01:

                await update.message.reply_text(
                    "⚠️ Сумма чека больше остатка "
                    "выданных денег.\n\n"
                    f"💳 Выдано: "
                    f"{transfer.amount:,.2f} ₽\n"
                    f"💸 Уже потрачено: "
                    f"{already_spent:,.2f} ₽\n"
                    f"💵 Остаток: "
                    f"{remaining:,.2f} ₽\n"
                    f"🧾 Чек: "
                    f"{amount:,.2f} ₽\n\n"
                    "Чек сохранён, но операция пока "
                    "не создана."
                )

                context.user_data[
                    "waiting_receipt_amount"
                ] = receipt.id

                return

        # =================================================
        # ОПРЕДЕЛЯЕМ КАТЕГОРИЮ
        # =================================================

        category_text = ""

        if receipt.shop_name:
            category_text += (
                receipt.shop_name + " "
            )

        if receipt.ocr_text:
            category_text += (
                receipt.ocr_text + " "
            )

        if transfer:

            category_text += (
                transfer.purpose or ""
            )

        category = detect_category(
            category_text
        )

        # =================================================
        # ОПИСАНИЕ
        # =================================================

        if receipt.shop_name:

            description = (
                f"Чек: {receipt.shop_name}"
            )

        elif transfer:

            description = (
                f"Чек по выдаче: "
                f"{transfer.purpose}"
            )

        else:

            description = (
                "Расход по чеку"
            )

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

        # =================================================
        # СТАТУС ВЫДАЧИ
        # =================================================

        transfer_message = ""

        if transfer:

            update_transfer_status(
                db,
                transfer
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

            if transfer.status == "completed":

                transfer_message = (
                    "\n\n"
                    "💳 Выдача закрыта.\n"
                    f"Выдано: "
                    f"{transfer.amount:,.2f} ₽\n"
                    f"Потрачено: "
                    f"{spent_amount:,.2f} ₽\n"
                    f"Остаток: "
                    f"{remaining:,.2f} ₽"
                )

            else:

                transfer_message = (
                    "\n\n"
                    "💳 По выдаче:\n"
                    f"Выдано: "
                    f"{transfer.amount:,.2f} ₽\n"
                    f"Потрачено: "
                    f"{spent_amount:,.2f} ₽\n"
                    f"Остаток: "
                    f"{remaining:,.2f} ₽"
                )

        db.commit()

        context.user_data.pop(
            "waiting_receipt_amount",
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
            "✅ Чек обработан\n\n"
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
            f"{transfer_message}"
        )

    except Exception as error:

        db.rollback()

        print(
            "Ошибка создания операции по чеку:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось создать операцию "
            "по чеку.\n\n"
            f"Ошибка: {error}"
        )

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
        "step": "title",
        "items": []
    }

    await update.message.reply_text(
        "📦 Создание заявки\n\n"
        "Напишите, что необходимо купить.\n\n"
        "Например:\n"
        "Монитор для рабочего места"
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

    data = context.user_data.get(
        "purchase_request"
    )

    if not data:
        return False

    step = data.get("step")

    if step == "title":

        if not text.strip():

            await update.message.reply_text(
                "❌ Название заявки не может быть пустым."
            )

            return True

        data["title"] = text.strip()
        data["step"] = "description"

        await update.message.reply_text(
            "📝 Напишите описание заявки.\n\n"
            "Если описание не нужно, напишите:\n"
            "нет"
        )

        return True

    if step == "description":

        if text.lower() == "нет":

            data["description"] = None

        else:

            data["description"] = text

        data["step"] = "product"

        await update.message.reply_text(
            "📦 Теперь укажите товар.\n\n"
            "Например:\n"
            "Монитор Samsung 24"
        )

        return True

    if step == "product":

        if not text.strip():

            await update.message.reply_text(
                "❌ Название товара не может быть пустым."
            )

            return True

        data["product_name"] = text.strip()
        data["step"] = "quantity"

        await update.message.reply_text(
            "🔢 Укажите количество.\n\n"
            "Например:\n"
            "2"
        )

        return True

    if step == "quantity":

        try:

            quantity = float(
                text.replace(",", ".")
            )

            if quantity <= 0:
                raise ValueError

        except ValueError:

            await update.message.reply_text(
                "❌ Количество должно быть числом "
                "больше нуля.\n\n"
                "Например: 2"
            )

            return True

        data["quantity"] = quantity
        data["step"] = "price"

        await update.message.reply_text(
            "💰 Укажите примерную цену "
            "за одну единицу.\n\n"
            "Если цена неизвестна, напишите:\n"
            "нет"
        )

        return True

    if step == "price":

        if text.lower() == "нет":

            data["estimated_price"] = None

        else:

            try:

                price = float(
                    text.replace(",", ".")
                )

                if price < 0:
                    raise ValueError

                data["estimated_price"] = price

            except ValueError:

                await update.message.reply_text(
                    "❌ Цена должна быть числом.\n\n"
                    "Например: 25000\n"
                    "или напишите: нет"
                )

                return True

        data["step"] = "category"

        await update.message.reply_text(
            "📂 Укажите категорию.\n\n"
            "Например:\n"
            "Оборудование\n"
            "Канцелярия\n"
            "Материалы\n"
            "Прочее"
        )

        return True

    if step == "category":

        if not text.strip():

            await update.message.reply_text(
                "❌ Укажите категорию."
            )

            return True

        data["category"] = text.strip()
        data["step"] = "priority"

        await update.message.reply_text(
            "⚡ Укажите приоритет:\n\n"
            "1 — низкий\n"
            "2 — обычный\n"
            "3 — высокий\n"
            "4 — срочный"
        )

        return True

    if step == "priority":

        priorities = {
            "1": "low",
            "2": "normal",
            "3": "high",
            "4": "urgent",
            "низкий": "low",
            "обычный": "normal",
            "нормальный": "normal",
            "высокий": "high",
            "срочный": "urgent"
        }

        priority = priorities.get(
            text.lower()
        )

        if priority is None:

            await update.message.reply_text(
                "❌ Выберите приоритет:\n\n"
                "1 — низкий\n"
                "2 — обычный\n"
                "3 — высокий\n"
                "4 — срочный"
            )

            return True

        data["priority"] = priority

        await save_purchase_request(
            update,
            context,
            user
        )

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

        item = PurchaseRequestItem(
            request_id=purchase_request.id,
            product_name=data["product_name"],
            quantity=data["quantity"],
            unit="шт.",
            estimated_price=data.get(
                "estimated_price"
            )
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

        await update.message.reply_text(
            "✅ Заявка создана!\n\n"
            f"📌 Номер: "
            f"{purchase_request.number}\n"
            f"📦 {purchase_request.title}\n"
            f"Товар: "
            f"{data['product_name']}\n"
            f"Количество: "
            f"{data['quantity']}\n"
            f"Категория: "
            f"{data['category']}\n"
            f"Приоритет: "
            f"{priority_names.get(data['priority'], data['priority'])}\n\n"
            "Заявка отправлена директору."
        )

        if DIRECTOR_TELEGRAM_ID:

            notification_text = (
                "📦 НОВАЯ ЗАЯВКА\n\n"
                f"📌 Номер: "
                f"{purchase_request.number}\n"
                f"👤 Сотрудник: "
                f"{user.full_name}\n"
                f"📦 Заявка: "
                f"{purchase_request.title}\n"
                f"🛒 Товар: "
                f"{data['product_name']}\n"
                f"🔢 Количество: "
                f"{data['quantity']}\n"
                f"📂 Категория: "
                f"{data['category']}\n"
                f"⚡ Приоритет: "
                f"{priority_names.get(data['priority'], data['priority'])}"
            )

            try:

                await context.bot.send_message(
                    chat_id=(
                        DIRECTOR_TELEGRAM_ID
                    ),
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
                "👤 Активных сотрудников нет."
            )

            return

        lines = [
            "👤 Сотрудники:\n"
        ]

        for employee in employees:

            lines.append(
                f"• {employee.full_name}\n"
                f"  Telegram ID: "
                f"{employee.telegram_id}"
            )

        await update.message.reply_text(
            "\n".join(lines)
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

    lower = text.lower()

    # =====================================================
    # ВЫДАЧА ДЕНЕГ СОТРУДНИКУ
    # =====================================================

    is_transfer = (
        "скинул" in lower
        or "скинула" in lower
        or "перевел" in lower
        or "перевёл" in lower
        or "выдал" in lower
        or "выдала" in lower
        or "перечислил" in lower
        or "перечислила" in lower
    )

    if is_transfer:

        amount = extract_amount(
            text
        )

        if amount is None:

            await update.message.reply_text(
                "❌ Не смогла определить сумму.\n\n"
                "Напиши, например:\n"
                "скинул Иванову 20000 на материалы"
            )

            return

        db = get_db()

        try:

            employee = find_employee_in_text(
                db,
                text
            )

            if not employee:

                await update.message.reply_text(
                    "❌ Не смогла определить сотрудника.\n\n"
                    "Напиши ФИО сотрудника, например:\n"
                    "скинул Иванову Ивану 20000 "
                    "на материалы"
                )

                return

            purpose = text

            phrases = [
                "скинул",
                "скинула",
                "перевел",
                "перевёл",
                "выдал",
                "выдала",
                "перечислил",
                "перечислила"
            ]

            for phrase in phrases:

                purpose = re.sub(
                    phrase,
                    "",
                    purpose,
                    flags=re.IGNORECASE
                )

            purpose = purpose.strip()

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
        or "пришло" in lower
        or "поступило" in lower
        or "получили" in lower
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
        or "купил" in lower
        or "купила" in lower
        or "оплатил" in lower
        or "оплатила" in lower
        or "заплатил" in lower
        or "заплатила" in lower
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
        "скинул Иванову 20000 на материалы\n\n"
        "💸 Купить напрямую:\n"
        "купил принтер 35000\n\n"
        "💰 Доход:\n"
        "пришло 500000 от клиента"
    )


# =========================================================
# СОТРУДНИК — ОБЫЧНЫЙ РАСХОД
# =========================================================

async def handle_employee_text(
    update: Update,
    text: str
):

    amount = extract_amount(
        text
    )

    if amount is None:

        await update.message.reply_text(
            "❌ Не смогла найти сумму.\n\n"
            "Напиши, например:\n"
            "потратил 3500 на бензин"
        )

        return

    telegram_user = update.effective_user

    if not telegram_user:
        return

    user = get_or_create_telegram_user(
        telegram_user
    )

    if not user:
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

        db.flush()

        employee_expense = EmployeeExpense(
            telegram_user_id=user.telegram_id,
            transaction_id=transaction.id,
            description=text
        )

        db.add(
            employee_expense
        )

        db.commit()

    except Exception as error:

        db.rollback()

        print(
            "Ошибка записи расхода сотрудника:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось записать расход.\n\n"
            f"Ошибка: {error}"
        )

        return

    finally:

        db.close()

    await update.message.reply_text(
        "✅ Расход записан\n\n"
        f"👤 Сотрудник: "
        f"{user.full_name}\n"
        f"💰 Сумма: "
        f"{amount:,.2f} ₽\n"
        f"📂 Категория: "
        f"{category}\n"
        f"📝 {text}"
    )


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
            "Введите, например:\n"
            "3500\n"
            "или\n"
            "3500.50"
        )

        return True

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

    if context.user_data.get(
        "awaiting_full_name"
    ):

        full_name = normalize_full_name(
            text
        )

        if not is_valid_full_name(
            full_name
        ):

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

                db.add(
                    user
                )

                db.commit()

                db.refresh(
                    user
                )

            else:

                user.full_name = full_name

                db.commit()

                db.refresh(
                    user
                )

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
    # ВВОД СУММЫ ПО ЧЕКУ
    # =====================================================

    if context.user_data.get(
        "waiting_receipt_amount"
    ):

        handled = (
            await handle_waiting_receipt_amount(
                update,
                context,
                text
            )
        )

        if handled:
            return

    # =====================================================
    # ГЛАВНОЕ МЕНЮ
    # =====================================================

    if text == "🏠 Главное меню":

        context.user_data.pop(
            "purchase_request",
            None
        )

        context.user_data.pop(
            "waiting_receipt_amount",
            None
        )

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

            await update.message.reply_text(
                "🧾 Отправьте следующим сообщением:\n\n"
                "📷 фотографию чека\n"
                "или\n"
                "📄 файл из банка — PDF, JPG, PNG и т.д."
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

            handled = (
                await handle_purchase_request_step(
                    update,
                    context,
                    user,
                    text
                )
            )

            if handled:
                return

        # -------------------------------------------------
        # ОБЫЧНЫЙ РАСХОД
        # -------------------------------------------------

        await handle_employee_text(
            update,
            text
        )

        return

    # =====================================================
    # ДИРЕКТОР
    # =====================================================

    if user.role == "director":

        if text == "💰 Финансы":

            await director_finances(
                update
            )

            return

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
        # ДРУГОЙ ТЕКСТ ДИРЕКТОРА
        # -------------------------------------------------

        await handle_director_text(
            update,
            text
        )


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

    TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY")

    builder = ApplicationBuilder().token(BOT_TOKEN)

    if TELEGRAM_PROXY:
        builder = builder.proxy(TELEGRAM_PROXY)
        builder = builder.get_updates_proxy(TELEGRAM_PROXY)

    application = builder.build()

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