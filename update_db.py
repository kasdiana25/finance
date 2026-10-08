import sqlite3

DB_PATH = "finance.db"

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

columns = [
    ("income_source", "VARCHAR"),
    ("account", "VARCHAR"),
    ("employee_id", "INTEGER"),
    ("paid_to", "VARCHAR"),
]

for column, column_type in columns:
    try:
        cursor.execute(
            f"ALTER TABLE transactions ADD COLUMN {column} {column_type}"
        )
        print(f"Добавлен столбец: {column}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print(f"Уже существует: {column}")
        else:
            raise

conn.commit()
conn.close()

print("База данных обновлена.")