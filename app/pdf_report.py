from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


def get_font_path():
    possible_paths = [
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/ARIAL.TTF"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    ]

    for path in possible_paths:
        if path.exists():
            return path

    return None


font_path = get_font_path()

if font_path:
    pdfmetrics.registerFont(
        TTFont("ReportFont", str(font_path))
    )

    FONT_NAME = "ReportFont"
else:
    FONT_NAME = "Helvetica"


def create_financial_pdf(report):

    buffer = BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=FONT_NAME,
        fontSize=20,
        leading=24,
        alignment=TA_CENTER,
        spaceAfter=12,
    )

    heading_style = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName=FONT_NAME,
        fontSize=14,
        leading=18,
        spaceBefore=10,
        spaceAfter=8,
    )

    normal_style = ParagraphStyle(
        "ReportNormal",
        parent=styles["Normal"],
        fontName=FONT_NAME,
        fontSize=9,
        leading=12,
    )

    small_style = ParagraphStyle(
        "ReportSmall",
        parent=styles["Normal"],
        fontName=FONT_NAME,
        fontSize=8,
        leading=10,
    )

    elements = []

    # Заголовок

    elements.append(
        Paragraph(
            "Финансовый отчёт",
            title_style
        )
    )

    elements.append(
        Paragraph(
            f"Период: "
            f"{report.date_from.strftime('%d.%m.%Y')} — "
            f"{report.date_to.strftime('%d.%m.%Y')}",
            normal_style
        )
    )

    elements.append(Spacer(1, 8 * mm))


    # Основные показатели

    elements.append(
        Paragraph(
            "Основные показатели",
            heading_style
        )
    )

    statistics_data = [
        [
            Paragraph("<b>Показатель</b>", normal_style),
            Paragraph("<b>Сумма</b>", normal_style),
        ],
        [
            Paragraph("Доходы", normal_style),
            Paragraph(
                f"{report.income:,.2f} ₽".replace(",", " "),
                normal_style
            ),
        ],
        [
            Paragraph("Расходы", normal_style),
            Paragraph(
                f"{report.expense:,.2f} ₽".replace(",", " "),
                normal_style
            ),
        ],
        [
            Paragraph("Чистый результат", normal_style),
            Paragraph(
                f"{report.balance:,.2f} ₽".replace(",", " "),
                normal_style
            ),
        ],
    ]

    statistics_table = Table(
        statistics_data,
        colWidths=[
            90 * mm,
            80 * mm,
        ],
    )

    statistics_table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#f1f5f9"),
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.HexColor("#dbe3ef"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
            ]
        )
    )

    elements.append(statistics_table)


    # Краткая статистика

    elements.append(
        Paragraph(
            "Краткая статистика",
            heading_style
        )
    )

    short_data = [
        [
            Paragraph("Показатель", normal_style),
            Paragraph("Количество", normal_style),
        ],
        [
            Paragraph("Операции", normal_style),
            Paragraph(
                str(report.transaction_count),
                normal_style
            ),
        ],
        [
            Paragraph("Выдано сотрудникам", normal_style),
            Paragraph(
                f"{report.transfer_total:,.2f} ₽".replace(",", " "),
                normal_style
            ),
        ],
        [
            Paragraph("Чеки", normal_style),
            Paragraph(
                str(report.receipt_count),
                normal_style
            ),
        ],
        [
            Paragraph("Заявки на закупку", normal_style),
            Paragraph(
                str(report.purchase_request_count),
                normal_style
            ),
        ],
    ]

    short_table = Table(
        short_data,
        colWidths=[
            90 * mm,
            80 * mm,
        ],
    )

    short_table.setStyle(
        TableStyle(
            [
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.HexColor("#dbe3ef"),
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#f1f5f9"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    8,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
            ]
        )
    )

    elements.append(short_table)


    # Операции

    elements.append(
        Paragraph(
            "Операции за период",
            heading_style
        )
    )

    transactions_data = [
        [
            Paragraph("<b>Дата</b>", small_style),
            Paragraph("<b>Тип</b>", small_style),
            Paragraph("<b>Категория</b>", small_style),
            Paragraph("<b>Сумма</b>", small_style),
            Paragraph("<b>Описание</b>", small_style),
        ]
    ]

    for transaction in report.transactions:

        transaction_type = (
            "Доход"
            if transaction.type == "income"
            else "Расход"
        )

        amount = float(transaction.amount or 0)

        if transaction.type == "expense":
            amount_text = f"-{amount:,.2f} ₽"
        else:
            amount_text = f"+{amount:,.2f} ₽"

        transactions_data.append(
            [
                Paragraph(
                    str(transaction.date),
                    small_style
                ),
                Paragraph(
                    transaction_type,
                    small_style
                ),
                Paragraph(
                    str(transaction.category or "—"),
                    small_style
                ),
                Paragraph(
                    amount_text.replace(",", " "),
                    small_style
                ),
                Paragraph(
                    str(transaction.description or "—"),
                    small_style
                ),
            ]
        )


    if len(transactions_data) == 1:

        transactions_data.append(
            [
                Paragraph(
                    "Операций за выбранный период нет.",
                    small_style
                ),
                "",
                "",
                "",
                "",
            ]
        )


    transactions_table = Table(
        transactions_data,
        colWidths=[
            25 * mm,
            22 * mm,
            35 * mm,
            30 * mm,
            58 * mm,
        ],
        repeatRows=1,
    )

    transactions_table.setStyle(
        TableStyle(
            [
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.4,
                    colors.HexColor("#dbe3ef"),
                ),
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#f1f5f9"),
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "TOP",
                ),
                (
                    "LEFTPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "RIGHTPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
            ]
        )
    )

    elements.append(transactions_table)


    # Генерация

    document.build(elements)

    buffer.seek(0)

    return buffer