import re
from datetime import datetime

import pytesseract
from PIL import Image, ImageOps, ImageEnhance


# ---------------------------------------------------------
# Tesseract
# ---------------------------------------------------------

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


# ---------------------------------------------------------
# Распознавание чека
# ---------------------------------------------------------

def recognize_receipt(filepath: str):
    image = Image.open(filepath)

    # Переводим изображение в оттенки серого
    image = ImageOps.grayscale(image)

    # Повышаем контраст
    image = ImageEnhance.Contrast(image).enhance(2.0)

    # OCR
    text = pytesseract.image_to_string(
        image,
        lang="rus+eng",
        config="--psm 6"
    )

    amount = extract_amount(text)
    receipt_date = extract_date(text)
    shop_name = extract_shop(text)

    return {
        "text": text,
        "amount": amount,
        "date": receipt_date,
        "shop_name": shop_name
    }


# ---------------------------------------------------------
# Сумма
# ---------------------------------------------------------

def extract_amount(text: str):
    if not text:
        return None

    # Приводим текст к нижнему регистру
    normalized = text.lower()

    # Частые варианты обозначения рублей
    normalized = normalized.replace("₽", " руб ")
    normalized = normalized.replace("р.", " руб ")
    normalized = normalized.replace("р ", " руб ")

    # Нормализуем пробелы
    normalized = re.sub(r"[ \t]+", " ", normalized)

    # -----------------------------------------------------
    # 1. Ищем ИТОГО / ВСЕГО / К ОПЛАТЕ / СУММА
    # -----------------------------------------------------

    total_patterns = [
        # итог =89.20
        r"\bитог\b\s*[:=\-]?\s*(\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?)",

        # итого 89.20
        r"\bитого\b\s*[:=\-]?\s*(\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?)",

        # всего 89.20
        r"\bвсего\b\s*[:=\-]?\s*(\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?)",

        # к оплате 89.20
        r"\bк\s+оплате\b\s*[:=\-]?\s*(\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?)",

        # сумма 89.20
        r"\bсумма\b\s*[:=\-]?\s*(\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?)",
    ]

    for pattern in total_patterns:
        match = re.search(
            pattern,
            normalized,
            flags=re.IGNORECASE
        )

        if match:
            amount = parse_amount(match.group(1))

            if amount is not None:
                return amount

    # -----------------------------------------------------
    # 2. Более гибкий поиск суммы после ключевого слова
    # -----------------------------------------------------

    fuzzy_patterns = [
        r"\bитог\b.{0,30}?(\d+(?:[.,]\d{1,2})?)",
        r"\bитого\b.{0,30}?(\d+(?:[.,]\d{1,2})?)",
        r"\bвсего\b.{0,30}?(\d+(?:[.,]\d{1,2})?)",
        r"\bсумм\w*\b.{0,30}?(\d+(?:[.,]\d{1,2})?)",
        r"\bк\s+оплате\b.{0,30}?(\d+(?:[.,]\d{1,2})?)",
    ]

    for pattern in fuzzy_patterns:
        match = re.search(
            pattern,
            normalized,
            flags=re.IGNORECASE
        )

        if match:
            amount = parse_amount(match.group(1))

            if amount is not None:
                return amount

    # -----------------------------------------------------
    # 3. Сумма рядом с ₽ / руб / рублей
    # -----------------------------------------------------

    currency_patterns = [
        r"(\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей)?|р(?:\.|\s))",

        r"(?:₽|руб(?:\.|лей)?|р(?:\.|\s))\s*(\d+(?:[ \t]\d{3})*(?:[.,]\d{1,2})?)",
    ]

    for pattern in currency_patterns:
        matches = re.findall(
            pattern,
            normalized,
            flags=re.IGNORECASE
        )

        for value in matches:
            amount = parse_amount(value)

            if amount is not None:
                return amount

    # -----------------------------------------------------
    # 4. Числа с копейками
    # -----------------------------------------------------

    decimal_pattern = (
        r"\b"
        r"(\d+(?:[ \t]\d{3})*[.,]\d{2})"
        r"\b"
    )

    matches = re.findall(
        decimal_pattern,
        normalized
    )

    amounts = []

    for value in matches:
        amount = parse_amount(value)

        if amount is not None:
            amounts.append(amount)

    if amounts:
        return amounts[-1]

    # -----------------------------------------------------
    # 5. Целые числа
    # -----------------------------------------------------

    integer_pattern = r"\b(\d{2,6})\b"

    matches = re.findall(
        integer_pattern,
        normalized
    )

    amounts = []

    for value in matches:
        try:
            amount = float(value)

            # Отбрасываем маленькие числа,
            # которые часто являются количеством,
            # номером кассы и т.д.
            if amount >= 10:
                amounts.append(amount)

        except ValueError:
            pass

    if amounts:
        return amounts[-1]

    return None


# ---------------------------------------------------------
# Преобразование суммы
# ---------------------------------------------------------

def parse_amount(value: str):
    if not value:
        return None

    value = value.strip()

    # Убираем пробелы между тысячами
    value = value.replace(" ", "")

    # Запятая -> точка
    value = value.replace(",", ".")

    # Оставляем только цифры и точку
    value = re.sub(
        r"[^0-9.]",
        "",
        value
    )

    if not value:
        return None

    try:
        amount = float(value)

        if amount <= 0:
            return None

        return amount

    except ValueError:
        return None


# ---------------------------------------------------------
# Дата
# ---------------------------------------------------------

def extract_date(text: str):
    if not text:
        return None

    patterns = [
        r"\b(\d{2}[./-]\d{2}[./-]\d{4})\b",
        r"\b(\d{2}[./-]\d{2}[./-]\d{2})\b"
    ]

    for pattern in patterns:
        matches = re.findall(
            pattern,
            text
        )

        for value in matches:
            for fmt in (
                "%d.%m.%Y",
                "%d/%m/%Y",
                "%d-%m-%Y",
                "%d.%m.%y",
                "%d/%m/%y",
                "%d-%m-%y"
            ):
                try:
                    return datetime.strptime(
                        value,
                        fmt
                    ).date()

                except ValueError:
                    pass

    return None


# ---------------------------------------------------------
# Магазин
# ---------------------------------------------------------

def extract_shop(text: str):
    if not text:
        return None

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    # Слова, которые обычно не являются названием магазина
    ignored_words = {
        "чек",
        "кассовый чек",
        "кассовый",
        "чек коррекции",
        "приход",
        "расход",
        "итог",
        "итого",
        "всего",
        "сумма",
        "к оплате",
        "дата",
        "время"
    }

    for line in lines[:8]:

        clean_line = line.strip()

        if len(clean_line) < 3:
            continue

        if clean_line.lower() in ignored_words:
            continue

        # Не считаем названием магазина строку,
        # состоящую только из цифр и знаков
        if re.fullmatch(
            r"[\d\s.,:-]+",
            clean_line
        ):
            continue

        return clean_line[:255]

    return None