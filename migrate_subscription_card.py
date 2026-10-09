import sqlite3
from pathlib import Path

db_path = Path(__file__).resolve().parent / "finance.db"

with sqlite3.connect(db_path) as connection:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }

    if "recurring_transactions" not in tables:
        raise RuntimeError(
            "Таблица recurring_transactions не найдена"
        )

    if "transactions" not in tables:
        raise RuntimeError("Таблица transactions не найдена")

    def add_column_if_missing(table, column, definition):
        columns = {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({table})"
            )
        }

        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} "
                f"ADD COLUMN {column} {definition}"
            )
            print(f"Добавлен столбец: {table}.{column}")
        else:
            print(f"Уже существует: {table}.{column}")

    add_column_if_missing(
        "recurring_transactions", "account", "VARCHAR"
    )
    add_column_if_missing(
        "recurring_transactions", "card_name", "VARCHAR"
    )
    add_column_if_missing(
        "transactions", "card_name", "VARCHAR"
    )

print("Миграция завершена.")