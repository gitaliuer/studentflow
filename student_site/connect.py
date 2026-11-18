
import sqlite3
from pathlib import Path

# Находим базу данных рядом с Python-файлом
db_path = Path(__file__).resolve().parent / "ак.db"

if not db_path.exists():
    raise FileNotFoundError("База ак.db не найдена")

# Подключаемся к базе
conn = sqlite3.connect(db_path)

cursor = conn.cursor()

cursor.execute("""
    SELECT
        u.full_name,
        s.group_name,
        sub.name,
        g.score
    FROM grades g
    JOIN students s ON g.student_id = s.id
    JOIN users u ON s.user_id = u.id
    JOIN subjects sub ON g.subject_id = sub.id
""")

for row in cursor.fetchall():
    print("Студент:", row[0])
    print("Группа:", row[1])
    print("Предмет:", row[2])
    print("Оценка:", row[3])

conn.close()
