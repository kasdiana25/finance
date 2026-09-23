import os
import re
import uuid
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from ..database import SessionLocal
from ..models import (
    Transaction,
    TelegramUser,
    EmployeeExpense,
    Receipt,
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
        DIRECTOR_TELEGRAM_ID = int(DIRECTOR_TELEGRAM_ID)
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
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def get_full_name(user):
    """
    Получает имя пользователя Telegram.
    """

    name = user.full_name

    if not name:
        name = user.username or "Пользователь"

    return name


def get_or_create_telegram_user(telegram_user):
    """
    Находит пользователя в БД или создаёт его.
    """

    db = get_db()

    try:
        user = get_user(
            db,
            telegram_user.id
        )

        if user:
            return user

        role = "employee"

        if (
            DIRECTOR_TELEGRAM_ID
            and telegram_user.id == DIRECTOR_TELEGRAM_ID
        ):
            role = "director"

        return create_user(
            db=db,
            telegram_id=telegram_user.id,
            full_name=get_full_name(
                telegram_user
            ),
            role=role
        )

    finally:
        db.close()


def detect_category(text: str):
    """
    Определяет категорию расхода по тексту.
    """

    lower = text.lower()

    # Транспорт
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

    # Материалы
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

    # Канцелярия
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

    # Питание
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

    # Связь
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

    # Аренда
    if "аренд" in lower:
        return "Аренда"

    # Зарплата
    if "зарплат" in lower or "аванс" in lower:
        return "Зарплата"

    # Реклама
    advertising_words = [
        "реклам",
        "продвижен",
        "таргет"
    ]

    for word in advertising_words:
        if word in lower:
            return "Реклама"

    return "Прочие расходы"


def extract_amount(text: str):
    """
    Извлекает сумму из сообщения.

    Примеры:

    потратил 3500 на бензин
    -> 3500.0

    потратил 3500,50 на бензин
    -> 3500.50

    купил материалы за 12500 рублей
    -> 12500.0
    """

    text = text.replace(",", ".")

    matches = re.findall(
        r"(?<!\d)(\d+(?:\.\d{1,2})?)(?!\d)",
        text
    )

    if not matches:
        return None

    try:
        values = [
            float(value)
            for value in matches
        ]

        return max(values)

    except ValueError:
        return None


# =========================================================
# ОБРАБОТЧИК ОШИБОК
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Общий обработчик ошибок Telegram.
    """

    print("\n❌ ОШИБКА TELEGRAM-БОТА:")

    try:
        print(context.error)
    except Exception:
        print("Не удалось вывести информацию об ошибке.")


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

    user = get_or_create_telegram_user(
        telegram_user
    )

    if user.role == "director":

        text = (
            "👑 <b>Finance Max Alice</b>\n\n"
            "Вы вошли как <b>директор</b>.\n\n"

            "Вы можете написать:\n\n"

            "💸 <code>скинул Иванову 20000 на материалы</code>\n"
            "💰 <code>пришло 500000 от клиента</code>\n"
            "💵 <code>выдал Иванову зарплату 70000</code>\n\n"

            "Команды:\n"
            "/me — мой профиль\n"
            "/id — мой Telegram ID\n"
            "/summary — финансовая сводка"
        )

    else:

        text = (
            "👤 <b>Finance Max Alice</b>\n\n"
            "Вы вошли как <b>сотрудник</b>.\n\n"

            "Вы можете написать:\n\n"

            "💳 <code>потратил 3500 на бензин</code>\n"
            "📝 <code>купил материалы за 5000</code>\n"
            "🍽 <code>потратил 1200 на обед</code>\n\n"

            "или просто отправить 📷 фотографию чека.\n\n"

            "Команды:\n"
            "/me — мой профиль\n"
            "/id — мой Telegram ID"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


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

    role_name = (
        "Директор"
        if user.role == "director"
        else "Сотрудник"
    )

    await update.message.reply_text(
        "👤 <b>Ваш профиль</b>\n\n"
        f"Имя: {user.full_name}\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"Роль: {role_name}",
        parse_mode="HTML"
    )


# =========================================================
# ФОТО ЧЕКА
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

    # Только сотрудник
    if user.role != "employee":

        await update.message.reply_text(
            "👑 Фотографии чеков через этот "
            "сценарий доступны сотрудникам."
        )

        return

    await update.message.reply_text(
        "🔎 Распознаю чек..."
    )

    try:

        photo = update.message.photo[-1]

        file = await context.bot.get_file(
            photo.file_id
        )

        filename = (
            f"{telegram_user.id}_"
            f"{uuid.uuid4().hex}.jpg"
        )

        filepath = (
            RECEIPTS_DIR / filename
        )

        await file.download_to_drive(
            custom_path=str(filepath)
        )

    except Exception as error:

        print(
            "Ошибка загрузки чека:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось сохранить фотографию чека."
        )

        return

    # =====================================================
    # OCR
    # =====================================================

    try:

        result = recognize_receipt(
            str(filepath)
        )

    except Exception as error:

        print(
            "Ошибка OCR:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось распознать чек.\n\n"
            f"Ошибка: {error}"
        )

        return

    amount = result.get("amount")

    receipt_date = (
        result.get("date")
        or date.today()
    )

    shop_name = result.get(
        "shop_name"
    )

    ocr_text = result.get(
        "text"
    )

    # =====================================================
    # Если сумма не найдена
    # =====================================================

    if amount is None:

        await update.message.reply_text(
            "⚠️ Чек сохранён, но я не смогла "
            "надёжно определить сумму.\n\n"

            "Напиши сумму сообщением, например:\n"

            "<code>потратил 3500</code>",

            parse_mode="HTML"
        )

        return

    # =====================================================
    # КАТЕГОРИЯ
    # =====================================================

    category_text = ""

    if shop_name:
        category_text += shop_name + " "

    if ocr_text:
        category_text += ocr_text

    category = detect_category(
        category_text
    )

    # =====================================================
    # СОХРАНЕНИЕ
    # =====================================================

    db = get_db()

    try:

        if shop_name:

            description = (
                f"Чек: {shop_name}"
            )

        else:

            description = (
                "Расход по чеку"
            )

        # -------------------------------------------------
        # Transaction
        # -------------------------------------------------

        transaction = Transaction(
            type="expense",
            category=category,
            amount=amount,
            description=description,
            date=receipt_date
        )

        db.add(transaction)

        db.commit()

        db.refresh(transaction)

        # -------------------------------------------------
        # EmployeeExpense
        # -------------------------------------------------

        employee_expense = EmployeeExpense(
            telegram_user_id=telegram_user.id,
            transaction_id=transaction.id,
            description=description
        )

        db.add(employee_expense)

        # -------------------------------------------------
        # Receipt
        # -------------------------------------------------

        receipt = Receipt(
            transaction_id=transaction.id,
            telegram_user_id=telegram_user.id,
            filename=filename,
            filepath=str(filepath),
            ocr_text=ocr_text,
            shop_name=shop_name,
            receipt_amount=amount,
            receipt_date=receipt_date
        )

        db.add(receipt)

        db.commit()

        shop_text = (
            shop_name
            if shop_name
            else "не определён"
        )

        await update.message.reply_text(
            "✅ <b>Чек обработан</b>\n\n"

            f"👤 Сотрудник: "
            f"{user.full_name}\n"

            f"🏪 Магазин: "
            f"{shop_text}\n"

            f"💰 Сумма: "
            f"{amount:,.2f} ₽\n"

            f"📅 Дата: "
            f"{receipt_date.strftime('%d.%m.%Y')}\n"

            f"📂 Категория: "
            f"{category}\n\n"

            "Расход добавлен "
            "в Finance Max Alice.",

            parse_mode="HTML"
        )

    except Exception as error:

        db.rollback()

        print(
            "Ошибка сохранения чека:",
            error
        )

        await update.message.reply_text(
            "❌ Не удалось сохранить расход.\n\n"
            f"Ошибка: {error}"
        )

    finally:

        db.close()


# =========================================================
# ТЕКСТОВЫЕ СООБЩЕНИЯ
# =========================================================

async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    # Защита от update без message
    if not update.message:
        return

    # Защита от сообщения без текста
    if not update.message.text:
        return

    telegram_user = update.effective_user

    if not telegram_user:
        return

    user = get_or_create_telegram_user(
        telegram_user
    )

    text = update.message.text.strip()

    if not text:
        return

    if user.role == "director":

        await handle_director_text(
            update,
            text
        )

    else:

        await handle_employee_text(
            update,
            text
        )


# =========================================================
# ДИРЕКТОР
# =========================================================

async def handle_director_text(
    update: Update,
    text: str
):

    lower = text.lower()

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

                "<code>пришло 500000 от клиента</code>",

                parse_mode="HTML"
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

            db.add(transaction)

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
            "✅ <b>Доход записан</b>\n\n"

            f"💰 Сумма: "
            f"{amount:,.2f} ₽\n"

            f"📝 {text}",

            parse_mode="HTML"
        )

        return

    # =====================================================
    # РАСХОД
    # =====================================================

    is_expense = (
        lower.startswith("-")
        or "скинул" in lower
        or "перевел" in lower
        or "перевёл" in lower
        or "выдал" in lower
    )

    if is_expense:

        amount = extract_amount(
            text
        )

        if amount is None:

            await update.message.reply_text(
                "❌ Не смогла определить сумму.\n\n"

                "Напиши, например:\n"

                "<code>скинул Иванову 20000 "
                "на материалы</code>",

                parse_mode="HTML"
            )

            return

        category = detect_category(
            text
        )

        # Для зарплаты явно устанавливаем категорию
        if "зарплат" in lower:
            category = "Зарплата"

        db = get_db()

        try:

            transaction = Transaction(
                type="expense",
                category=category,
                amount=amount,
                description=text,
                date=date.today()
            )

            db.add(transaction)

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
            "✅ <b>Расход записан</b>\n\n"

            f"💸 Сумма: "
            f"{amount:,.2f} ₽\n"

            f"📂 Категория: "
            f"{category}\n"

            f"📝 {text}",

            parse_mode="HTML"
        )

        return

    # =====================================================
    # НЕИЗВЕСТНАЯ КОМАНДА
    # =====================================================

    await update.message.reply_text(
        "Не поняла команду.\n\n"

        "Попробуйте:\n"

        "💸 <code>скинул Иванову 20000 "
        "на материалы</code>\n"

        "💰 <code>пришло 500000 от клиента</code>\n"

        "💵 <code>выдал Иванову зарплату "
        "70000</code>",

        parse_mode="HTML"
    )


# =========================================================
# СОТРУДНИК
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

            "<code>потратил 3500 "
            "на бензин</code>",

            parse_mode="HTML"
        )

        return

    telegram_user = update.effective_user

    if not telegram_user:
        return

    user = get_or_create_telegram_user(
        telegram_user
    )

    category = detect_category(
        text
    )

    db = get_db()

    try:

        # =================================================
        # TRANSACTION
        # =================================================

        transaction = Transaction(
            type="expense",
            category=category,
            amount=amount,
            description=text,
            date=date.today()
        )

        db.add(transaction)

        db.commit()

        db.refresh(transaction)

        # =================================================
        # EMPLOYEE EXPENSE
        # =================================================

        employee_expense = EmployeeExpense(
            telegram_user_id=user.telegram_id,
            transaction_id=transaction.id,
            description=text
        )

        db.add(employee_expense)

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
        "✅ <b>Расход записан</b>\n\n"

        f"👤 Сотрудник: "
        f"{user.full_name}\n"

        f"💰 Сумма: "
        f"{amount:,.2f} ₽\n"

        f"📂 Категория: "
        f"{category}\n"

        f"📝 {text}",

        parse_mode="HTML"
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

    if user.role != "director":

        await update.message.reply_text(
            "⛔ Эта команда доступна "
            "только директору."
        )

        return

    db = get_db()

    try:

        transactions = (
            db.query(
                Transaction
            ).all()
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
        "📊 <b>Финансовая сводка</b>\n\n"

        f"💰 Доходы: "
        f"{income:,.2f} ₽\n"

        f"💸 Расходы: "
        f"{expense:,.2f} ₽\n"

        f"💵 Баланс: "
        f"{balance:,.2f} ₽",

        parse_mode="HTML"
    )


# =========================================================
# ЗАПУСК
# =========================================================

def main():

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    # -----------------------------------------------------
    # ОБРАБОТЧИК ОШИБОК
    # -----------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    # -----------------------------------------------------
    # КОМАНДЫ
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # ФОТО ЧЕКА
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_receipt
        )
    )

    # -----------------------------------------------------
    # ТЕКСТ
    # -----------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    print(
        "🤖 Finance Max Alice Telegram Bot запущен..."
    )

    application.run_polling()


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()
