import sqlite3

connection = sqlite3.connect("finance.db")
cursor = connection.cursor()

tables = cursor.execute(
    """
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
      AND name IN ('products', 'inventory_items')
    """
).fetchall()

print("Найденные таблицы:", tables)

connection.close()