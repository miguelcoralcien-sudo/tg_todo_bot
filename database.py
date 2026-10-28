"""
database.py — весь код, который работает с базой данных SQLite.

Здесь нет ничего про Telegram: только таблица, SQL-запросы и экспорт в CSV.
Благодаря этому эту часть можно легко переиспользовать или заменить.

Важно: aiogram 3.x — полностью асинхронный. Поэтому вместо блокирующего
синхронного sqlite3 мы используем aiosqlite — тонкую асинхронную обёртку
над тем же самым sqlite3. SQL-код при этом остаётся обычным.
"""

import csv
import os
from datetime import datetime

import aiosqlite

# Имя файла базы данных. SQLite — это просто один файл на диске,
# который создаётся автоматически при первом подключении.
DB_FILE = "tasks.db"


class Database:
    """
    Асинхронный класс-помощник для работы с базой данных задач.

    В отличие от синхронной версии, здесь мы держим ОДНО долгоживущее
    соединение с базой: открываем его в start(), закрываем в close().
    Это эффективнее, чем переподключаться на каждый запрос, и при этом
    не блокирует event loop бота.
    """

    def __init__(self, db_file: str = DB_FILE):
        """
        Запоминаем, в каком файле лежит наша база.

        Сам файл здесь ещё не создаётся — он появится, когда мы впервые
        обратимся к базе (в start()).
        """
        self.db_file = db_file
        # Соединение пока не установлено — оно появится в start().
        self._db: aiosqlite.Connection | None = None

    async def start(self) -> None:
        """
        Открывает соединение с базой и создаёт таблицу tasks, если её нет.

        Должен быть вызван один раз при запуске бота (до start_polling).
        """
        # aiosqlite.connect возвращает объект соединения, который нужно
        # "дождаться", чтобы фоновый поток БД реально запустился.
        self._db = await aiosqlite.connect(self.db_file)

        # row_factory=aiosqlite.Row позволяет обращаться к колонкам
        # результата по имени (task["text"]), а не только по номеру —
        # так код читается гораздо легче.
        self._db.row_factory = aiosqlite.Row

        await self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                text       TEXT    NOT NULL,
                user       TEXT    NOT NULL,
                created_at TEXT    NOT NULL
            )
            """
        )

        # commit() обязателен: без него SQLite не сохранит CREATE TABLE
        # (aiosqlite работает в режиме транзакций по умолчанию).
        await self._db.commit()

    async def close(self) -> None:
        """
        Закрывает соединение с базой.

        Вызывается при остановке бота, чтобы корректно освободить ресурсы
        и фоновый поток, обслуживающий sqlite3.
        """
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def _conn(self) -> aiosqlite.Connection:
        """
        Внутренний помощник: возвращает соединение или падает с внятной
        ошибкой, если база ещё не была запущена через start().
        """
        if self._db is None:
            raise RuntimeError(
                "База данных не инициализирована. Сначала вызовите await db.start()."
            )
        return self._db

    async def add_task(self, text: str, user: str) -> int:
        """
        Добавляет одну задачу в таблицу tasks.

        Аргументы:
          text — текст задачи;
          user — имя того, кто её добавил.

        Возвращает id новой задачи (чтобы бот мог написать "Задача №5 добавлена").
        created_at генерируется здесь же — текущая дата и время.
        """
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Знак ? — плейсхолдер для параметра. Такой способ подстановки
        # защищает от SQL-инъекций: SQLite сам экранирует значения.
        cursor = await self._conn.execute(
            "INSERT INTO tasks (text, user, created_at) VALUES (?, ?, ?)",
            (text, user, created_at),
        )
        await self._conn.commit()

        # cursor.lastrowid — номер, который SQLite дал только что
        # добавленной записи.
        return cursor.lastrowid

    async def get_all_tasks(self) -> list:
        """
        Возвращает ВСЕ задачи из базы в виде списка.

        Каждый элемент — это строка с полями id, text, user, created_at.
        Сортировка по id: сначала старые задачи, потом новые.
        """
        cursor = await self._conn.execute(
            "SELECT id, text, user, created_at FROM tasks ORDER BY id"
        )
        # fetchall() собирает все найденные строки в один список.
        return await cursor.fetchall()

    async def get_tasks_count(self) -> int:
        """
        Возвращает количество задач в базе.

        Нужен, чтобы бот мог честно сказать "список пуст",
        не выгружая ради этого все задачи.
        """
        cursor = await self._conn.execute("SELECT COUNT(*) AS total FROM tasks")
        row = await cursor.fetchone()
        return row["total"]

    async def export_to_csv(self, csv_file: str = "tasks_export.csv") -> str:
        """
        Выгружает все задачи в CSV-файл и возвращает путь к нему.

        CSV — это обычный текстовый файл, где значения разделены точкой
        с запятой (чтобы Excel не путался), и который можно открыть
        в Excel или Google Таблицах.

        encoding="utf-8-sig" — важная деталь: к файлу добавляется BOM-метка,
        иначе Excel в Windows покажет русские буквы как "кракозябры".
        """
        tasks = await self.get_all_tasks()

        with open(csv_file, mode="w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file, delimiter=";", quoting=csv.QUOTE_MINIMAL)
            # Первая строка — заголовки колонок.
            writer.writerow(["id", "text", "user", "created_at"])
            # Дальше по одной строке на каждую задачу.
            for task in tasks:
                writer.writerow(
                    [task["id"], task["text"], task["user"], task["created_at"]]
                )

        # os.path.abspath превращает "tasks_export.csv" в полный путь к файлу —
        # так бот точно найдёт его при отправке.
        return os.path.abspath(csv_file)
