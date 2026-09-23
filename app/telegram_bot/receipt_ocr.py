import re
from datetime import datetime
from pathlib import Path

import pytesseract
from PIL import Image


TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH


def recognize_receipt(filepath: str):
    image = Image.open(filepath)

    text = pytesseract.image_to_string(
        image,
        lang="rus+eng"
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


def extract_amount(text: str):
    patterns = [
        r"(?:итого|всего|к оплате|сумма)\s*[:\-]?\s*([\d\s]+[,.]\d{2})",
        r"([\d\s]+[,.]\d{2})\s*(?:руб|р\.|₽)"
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:
            value = match.group(1)

            value = (
                value
                .replace(" ", "")
                .replace(",", ".")
            )

            try:
                return float(value)
            except ValueError:
                pass

    return None


def extract_date(text: str):
    patterns = [
        r"\b(\d{2}[./-]\d{2}[./-]\d{4})\b",
        r"\b(\d{2}[./-]\d{2}[./-]\d{2})\b"
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        value = match.group(1)

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


def extract_shop(text: str):
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    # В большинстве чеков название магазина
    # находится в первых строках.
    for line in lines[:5]:
        if len(line) >= 3:
            return line[:255]

    return None