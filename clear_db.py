import sqlite3

DB_PATH = "finance.db"

db = sqlite3.connect(DB_PATH)

tables = db.execute("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
      AND name NOT LIKE 'sqlite_%'
""").fetchall()

for (table,) in tables:
    db.execute(f'DELETE FROM "{table}"')

# Сбрасываем счетчики ID
try:
    db.execute("DELETE FROM sqlite_sequence")
except sqlite3.OperationalError:
    pass

db.commit()
db.close()

print("База полностью очищена!")