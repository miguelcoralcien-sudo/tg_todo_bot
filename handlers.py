"""
handlers.py — обработчики команд Telegram-бота.

Здесь живут:
  * FSM-состояния (машина состояний) для команды /add;
  * обработчики команд /start, /add, /list, /list_csv, /cancel.

Объект базы данных (db) приходит в каждый обработчик автоматически —
мы кладём его в workflow data при запуске диспетчера в main.py.
"""

import html

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import FSInputFile, Message

from database import Database

# =============================================================================
# Машина состояний (FSM)
# =============================================================================
#
# Проблема, которую решает FSM: после команды /add бот должен воспринять
# СЛЕДУЮЩЕЕ сообщение именно как текст задачи, а не как обычное сообщение.
#
# FSM хранит текущее состояние каждого диалога (user + chat) в памяти
# (или в Redis/БД, если настроить Storage). Мы просим диспетчер передать
# объект FSMContext в обработчик — через аннотацию `state: FSMContext`.
# =============================================================================


class AddTaskStates(StatesGroup):
    """Группа состояний для добавления задачи через /add."""

    # Одно-единственное состояние: бот задал вопрос и ждёт текст задачи.
    waiting_for_text = State()


# Роутер — «коробка» с обработчиками. Диспетчеру в main.py мы передаём
# именно её, а он уже раскладывает входящие сообщения по нужным хендлерам.
handlers_router = Router()


# =============================================================================
# /start — приветствие и список команд
# =============================================================================


@handlers_router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """
    Обработчик команды /start.

    CommandStart — встроенный фильтр aiogram 3, который ловит и /start,
    и нажатие кнопки Start в меню бота (в отличие от Command("start")).
    """
    # from_user может отсутствовать (например, у сообщений из каналов),
    # поэтому подстраховываемся и берём username, а иначе — общее обращение.
    user = message.from_user
    name = f"@{user.username}" if user and user.username else "друг"

    await message.answer(
        f"Привет, {name}! 👋\n\n"
        "Я бот-список задач. Вот что я умею:\n"
        "/add — добавить задачу\n"
        "/list — показать все задачи\n"
        "/list_csv — выгрузить задачи в CSV\n"
        "/cancel — отменить добавление задачи"
    )


# =============================================================================
# /add — начало диалога добавления задачи (шаг 1)
# =============================================================================


@handlers_router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    """
    Обработчик команды /add.

    Сам текст задачи мы пока не знаем — только включаем состояние
    ожидания и спрашиваем его у пользователя.
    """
    # Включаем состояние ожидания текста задачи.
    await state.set_state(AddTaskStates.waiting_for_text)

    await message.answer("Введите текст задачи 📝\n(отмена — /cancel)")


# =============================================================================
# Ответ в состоянии ожидания — это и есть текст задачи (шаг 2)
# =============================================================================
#
# Порядок регистрации хендлеров важен: диспетчер перебирает их сверху вниз.
# Фильтр по состоянию срабатывает только когда FSM включена, поэтому
# обычные сообщения вне /add сюда не попадают.
# =============================================================================


@handlers_router.message(AddTaskStates.waiting_for_text)
async def process_task_text(message: Message, state: FSMContext, db: Database) -> None:
    """
    Обрабатывает сообщение, полученное в состоянии AddTaskStates.waiting_for_text.

    Здесь текст пользователя превращается в запись в базе данных.
    """
    # Это может быть не только /cancel, но и любая другая команда,
    # поэтому сначала проверяем, что пользователь вообще прислал текст.
    if not message.text:
        await message.answer("Пришлите, пожалуйста, текстовое сообщение.")
        return

    text = message.text.strip()

    # Пустое сообщение (например, одни пробелы) в базу не пишем.
    if not text:
        await message.answer("Текст задачи пустой. Попробуйте ещё раз или /cancel.")
        return

    # Колонка user в нашей таблице — имя автора задачи.
    user = message.from_user
    author = (user.username or user.full_name) if user else "неизвестно"

    # Добавляем задачу и получаем её номер.
    task_id = await db.add_task(text=text, user=author)

    # Обязательно выходим из состояния, иначе следующее сообщение
    # пользователя снова ушло бы в этот обработчик.
    await state.clear()

    # html.escape защищает сообщение, если пользователь прислал "<b>привет</b>".
    await message.answer(f"✅ Задача №{task_id} добавлена:\n<i>{html.escape(text)}</i>")


# =============================================================================
# /cancel — выход из FSM
# =============================================================================


@handlers_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """
    Отменяет добавление задачи.

    Работает и вне FSM: state.clear() ничего не сломает, если
    состояние и так не было установлено.
    """
    await state.clear()
    await message.answer("Ок, отменил. Команды — /start")


# =============================================================================
# /list — список всех задач
# =============================================================================


@handlers_router.message(Command("list"))
async def cmd_list(message: Message, db: Database) -> None:
    """Показывает все задачи из базы одной свёрстанной «карточкой»."""
    # count() дешевле, чем выборка всех задач, — проверим пустоту по нему.
    if await db.get_tasks_count() == 0:
        await message.answer("Задач пока нет. Добавьте первую: /add")
        return

    tasks = await db.get_all_tasks()

    lines = [f"📋 <b>Задачи ({len(tasks)}):</b>", ""]
    for task in tasks:
        lines.append(
            f"<b>#{task['id']}</b> {html.escape(task['text'])}\n"
            f"     👤 {html.escape(task['user'])} · 🕒 {task['created_at']}"
        )

    text = "\n".join(lines)

    # Лимит текста сообщения в Telegram — 4096 символов.
    # Если список длиннее, режем его по границам строк и отправляем частями,
    # чтобы не разорвать задачу посередине.
    if len(text) <= 4096:
        await message.answer(text)
        return

    chunk = ""
    for line in lines:
        # +1 — символ переноса строки между строками.
        if len(chunk) + len(line) + 1 > 4096:
            await message.answer(chunk)
            chunk = ""
        chunk += line + "\n"

    if chunk.strip():
        await message.answer(chunk)


# =============================================================================
# /list_csv — выгрузка задач в CSV и отправка файла
# =============================================================================


@handlers_router.message(Command("list_csv"))
async def cmd_list_csv(message: Message, db: Database) -> None:
    """
    Выгружает задачи в CSV-файл и отправляет его пользователю документом.

    Саму выгрузку делает database.export_to_csv — handlers занимается
    только доставкой файла в Telegram.
    """
    if await db.get_tasks_count() == 0:
        await message.answer("Выгружать нечего: список задач пуст. Добавьте задачи — /add")
        return

    csv_path = await db.export_to_csv()

    # FSInputFile — способ отправить файл, который лежит на диске.
    # filename — то имя, которое увидит пользователь в Telegram.
    document = FSInputFile(csv_path, filename="tasks_export.csv")

    await message.answer_document(
        document,
        caption=f"📎 Выгружено задач: {await db.get_tasks_count()}",
    )
