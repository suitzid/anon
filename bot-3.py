"""
Orenix Anonim v2 — анонимный бот + админ-панель.

Первый админ (владелец) назначается командой  /claim <ADMIN_KEY>
(ADMIN_KEY — секретный ключ из переменной окружения, работает один раз,
пока в базе нет ни одного админа). Дальше админы хранятся в БД.

Запуск:
  pip install -r requirements.txt
  export BOT_TOKEN="токен_из_BotFather"
  export ADMIN_KEY="придумайте_длинный_секрет"
  python bot.py
"""
import asyncio
import hmac
import html
import logging
import os
import random
import re
import string
import time

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB = os.getenv("DB_PATH", "bot.db")

REVEAL_PRICE = 25
GIFT_MULTIPLIER = 2
MAX_GIFTS_SHOWN = 12

router = Router()   # пользовательская часть
adm = Router()      # админ-панель (доступ только админам из БД)

# ═════════════════════ ТЕКСТЫ И КНОПКИ (кастомизируются в админке) ═════════════════════
# key: (название, значение по умолчанию, доступные переменные, формат html|plain)
DEFAULTS = {
    "start_new": ("Старт: приветствие и ссылка",
        "🚀 <b>Начни получать анонимные сообщения прямо сейчас</b>\n\nТвоя ссылка 👇\n<code>{link}</code>\n\n"
        "Размести эту ссылку в описании профиля Telegram / TikTok / Instagram, чтобы начать получать анонимные сообщения 💬\n\n"
        "/mystats — статистика\n/url — изменить ссылку\n/issue — предложить идею", "{link}", "html"),
    "write_prompt": ("Приглашение написать сообщение",
        "✉️ Напишите анонимное сообщение — получатель не узнает, кто вы.\nИли отправьте подарок 🎁", "", "html"),
    "link_invalid": ("Ссылка недействительна", "❌ Ссылка недействительна или была изменена владельцем.", "", "html"),
    "own_link": ("Открыта своя ссылка", "Это ваша собственная ссылка 🙂 Отправьте её друзьям.", "", "html"),
    "sent_ok": ("Сообщение отправлено", "✅ Отправлено анонимно! Можете написать ещё или /start для выхода.", "", "html"),
    "deliver_fail": ("Не удалось доставить", "⚠️ Не удалось доставить: получатель недоступен.", "", "html"),
    "new_msg_title": ("Заголовок нового сообщения", "📨 <b>Новое анонимное сообщение</b>", "", "html"),
    "reply_title": ("Заголовок ответа", "💬 <b>Ответ на ваше сообщение</b>", "", "html"),
    "reply_prompt": ("Приглашение написать ответ", "✍️ Напишите ответ — он придёт анонимно.", "", "html"),
    "reply_sent": ("Ответ отправлен", "✅ Ответ отправлен!", "", "html"),
    "reply_fail": ("Ответ не доставлен", "⚠️ Не удалось доставить ответ.", "", "html"),
    "msg_unavailable": ("Сообщение недоступно (алерт)", "Сообщение недоступно", "", "plain"),
    "sender_info": ("Информация об отправителе", "👁 <b>Отправитель:</b> {name}{username}", "{name} {username}", "html"),
    "gifts_title": ("Выбор подарка", "🎁 Выберите подарок (цена для вас указана после стрелки):", "", "html"),
    "gifts_none": ("Нет подарков (алерт)", "Сейчас нет доступных подарков", "", "plain"),
    "gift_prompt": ("Запрос описания подарка", "✍️ Напишите описание к подарку (до 100 символов):", "", "html"),
    "gift_unavailable": ("Подарок недоступен (алерт)", "Подарок больше недоступен", "", "plain"),
    "gift_sent": ("Подарок отправлен", "🎁 Подарок отправлен анонимно!", "", "html"),
    "gift_failed": ("Ошибка подарка", "⚠️ Не удалось отправить подарок. Звёзды возвращены.", "", "html"),
    "gift_received": ("Уведомление получателю о подарке", "🎁 Вам пришёл анонимный подарок!", "", "html"),
    "gift_prefix": ("Подпись на подарке (до 128 симв.)", "Анонимно: {text}", "{text}", "plain"),
    "reveal_title": ("Счёт: заголовок раскрытия (до 32)", "Узнать отправителя", "", "plain"),
    "reveal_desc": ("Счёт: описание раскрытия", "Раскрыть, кто отправил это сообщение", "", "plain"),
    "reveal_label": ("Счёт: подпись цены раскрытия", "Раскрытие отправителя", "", "plain"),
    "gift_title": ("Счёт: заголовок подарка (до 32)", "Анонимный подарок", "", "plain"),
    "gift_desc": ("Счёт: описание подарка", "Подарок с вашим описанием: {text}", "{text}", "plain"),
    "gift_label": ("Счёт: подпись цены подарка", "Подарок", "", "plain"),
    "stats_text": ("Статистика профиля",
        "📊 <b>Статистика профиля</b>\n\n<b>Сегодня:</b> сообщений: {today}\n"
        "<b>За всё время:</b> сообщений: {total}, переходов по ссылке: {visits}\n\n👉 <code>{link}</code>",
        "{today} {total} {visits} {link}", "html"),
    "url_info": ("/url без аргумента",
        "Сейчас ваша ссылка: <code>{link}</code>\n\nЧтобы изменить: <code>/url Name</code>\n"
        "❗ Старая ссылка перестанет работать!", "{link}", "html"),
    "url_bad": ("/url: неверный формат",
        "❗ Ссылка: только английские буквы, цифры и _, длина 7–30.\nНапример: <code>/url MrDurov</code>", "", "html"),
    "url_taken": ("/url: имя занято", "❌ Это имя уже занято.", "", "html"),
    "url_done": ("/url: готово", "Готово! ✅\n\nВаша новая ссылка:\n🔗 <code>{link}</code>", "{link}", "html"),
    "issue_help": ("/issue без текста", '💡 Напишите "/issue Текст...", чтобы отправить нам идею.', "", "html"),
    "issue_thanks": ("/issue: спасибо", "Спасибо! Идея отправлена 🙌", "", "html"),
    "cancelled": ("Отмена", "Отменено.", "", "html"),
    "banned": ("Сообщение заблокированному", "🚫 Вы заблокированы в этом боте.", "", "html"),
    "disabled": ("Бот выключен", "🛠 Бот временно отключён. Попробуйте позже.", "", "html"),
    "sub_required": ("Требуется подписка",
        "📢 Чтобы пользоваться ботом, подпишитесь на каналы ниже и нажмите «Проверить».", "", "html"),
    "sub_fail": ("Подписка не найдена (алерт)", "❌ Вы ещё не подписались на все каналы.", "", "plain"),
    "sub_ok": ("Подписка подтверждена", "✅ Спасибо! Подписка подтверждена. Нажмите /start.", "", "html"),
    "support_text": ("Команда /support", "💬 Нужна помощь? Напишите в поддержку.", "", "html"),
    "link_annulled": ("Уведомление: ссылка аннулирована",
        "⚠️ Ваша ссылка была аннулирована администрацией.\nНовая ссылка: <code>{link}</code>", "{link}", "html"),
    "link_changed": ("Уведомление: ссылка изменена админом",
        "ℹ️ Администрация изменила вашу ссылку.\nНовая ссылка: <code>{link}</code>", "{link}", "html"),
    "btn_gift": ("Кнопка: подарок", "🎁 Отправить подарок", "", "plain"),
    "btn_cancel": ("Кнопка: отмена", "❌ Отмена", "", "plain"),
    "btn_reply": ("Кнопка: ответить", "↩️ Ответить", "", "plain"),
    "btn_reveal": ("Кнопка: узнать отправителя", "👁 Узнать отправителя ({price} ⭐)", "{price}", "plain"),
    "btn_support": ("Кнопка: поддержка", "🆘 Поддержка", "", "plain"),
    "btn_check_sub": ("Кнопка: проверить подписку", "✅ Проверить подписку", "", "plain"),
    "btn_channel": ("Кнопка: канал", "📢 {title}", "{title}", "plain"),
    "support_url": ("Ссылка поддержки", "https://t.me/OrenixSup", "", "plain"),
}
SETTINGS: dict[str, str] = {}   # кастомные значения + флаги (sys:enabled)
SUB_OK: dict[int, float] = {}   # кэш подписок на 60 сек


def txt(key: str, **kw) -> str:
    tpl = SETTINGS.get(key, DEFAULTS[key][1])
    return re.sub(r"\{(\w+)\}", lambda m: str(kw[m.group(1)]) if m.group(1) in kw else m.group(0), tpl)


def cb(text, data):
    return InlineKeyboardButton(text=text[:64], callback_data=data)


def ub(text, url):
    return InlineKeyboardButton(text=text[:64], url=url)


def ikb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def support_btn():
    return ub(txt("btn_support"), txt("support_url"))


class Writing(StatesGroup):
    text = State()


class Replying(StatesGroup):
    text = State()


class GiftText(StatesGroup):
    text = State()


class Adm(StatesGroup):
    broadcast = State()
    sub_chat = State()
    add_admin = State()
    ban = State()
    unban = State()
    annul = State()
    relink = State()
    edit_text = State()


class NotCommand(BaseFilter):
    async def __call__(self, m: Message) -> bool:
        return not (m.text and m.text.startswith("/"))


# ═════════════════════ БАЗА ДАННЫХ ═════════════════════

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, username TEXT,
                visits INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id INTEGER, receiver_id INTEGER,
                text TEXT, is_reply INTEGER DEFAULT 0, revealed INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS gifts(
                id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id INTEGER, target_id INTEGER,
                gift_id TEXT, price INTEGER, text TEXT, paid INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS admins(
                id INTEGER PRIMARY KEY, is_owner INTEGER DEFAULT 0, added_by INTEGER);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS channels(
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER UNIQUE,
                title TEXT, link TEXT, request INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS join_requests(
                chat_id INTEGER, user_id INTEGER, PRIMARY KEY(chat_id, user_id));
            CREATE TABLE IF NOT EXISTS payments(
                id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, user_id INTEGER,
                amount INTEGER, profit INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """
        )
        for table, col, ddl in [
            ("users", "banned", "INTEGER DEFAULT 0"),
            ("users", "joined_at", "TEXT"),
            ("gifts", "cost", "INTEGER DEFAULT 0"),
        ]:
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                cols = [r[1] for r in await cur.fetchall()]
            if col not in cols:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        await db.commit()
        async with db.execute("SELECT key, value FROM settings") as cur:
            for k, v in await cur.fetchall():
                SETTINGS[k] = v


async def fetchone(sql, args=()):
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return await cur.fetchone()


async def fetchall(sql, args=()):
    async with aiosqlite.connect(DB) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return await cur.fetchall()


async def execute(sql, args=()):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(sql, args)
        await db.commit()
        return cur.lastrowid


async def set_setting(k, v):
    SETTINGS[k] = v
    await execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))


async def del_setting(k):
    SETTINGS.pop(k, None)
    await execute("DELETE FROM settings WHERE key=?", (k,))


def gen_code(n=6):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


async def unique_code():
    while True:
        code = gen_code()
        if not await fetchone("SELECT id FROM users WHERE lower(code)=lower(?)", (code,)):
            return code


async def upsert_user(u):
    row = await fetchone("SELECT id FROM users WHERE id=?", (u.id,))
    if row:
        await execute("UPDATE users SET name=?, username=? WHERE id=?", (u.full_name, u.username, u.id))
        return
    await execute(
        "INSERT INTO users(id, code, name, username, joined_at) VALUES(?,?,?,?,datetime('now'))",
        (u.id, await unique_code(), u.full_name, u.username))


async def is_admin(uid: int) -> bool:
    return bool(await fetchone("SELECT 1 FROM admins WHERE id=?", (uid,)))


async def is_owner(uid: int) -> bool:
    return bool(await fetchone("SELECT 1 FROM admins WHERE id=? AND is_owner=1", (uid,)))


async def my_link(bot: Bot, user_id: int) -> str:
    row = await fetchone("SELECT code FROM users WHERE id=?", (user_id,))
    return f"t.me/{(await bot.me()).username}?start={row['code']}"


async def link_for_code(bot: Bot, code: str) -> str:
    return f"t.me/{(await bot.me()).username}?start={code}"


async def resolve_user(ref: str):
    """Ищет пользователя по ID, @username, коду ссылки или самой ссылке."""
    ref = ref.strip()
    m = re.search(r"start=(\w+)", ref)
    if m:
        return await fetchone("SELECT * FROM users WHERE lower(code)=lower(?)", (m.group(1),))
    if re.fullmatch(r"\d{5,15}", ref):
        return await fetchone("SELECT * FROM users WHERE id=?", (int(ref),))
    ref = ref.lstrip("@")
    return (await fetchone("SELECT * FROM users WHERE lower(username)=lower(?)", (ref,))
            or await fetchone("SELECT * FROM users WHERE lower(code)=lower(?)", (ref,)))


# ═════════════════════ ПОДПИСКА НА КАНАЛЫ ═════════════════════

async def missing_channels(bot: Bot, uid: int):
    missing = []
    for ch in await fetchall("SELECT * FROM channels"):
        ok = False
        try:
            m = await bot.get_chat_member(ch["chat_id"], uid)
            ok = m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR) \
                or (m.status == ChatMemberStatus.RESTRICTED and getattr(m, "is_member", False))
        except TelegramAPIError:
            ok = True  # бот не админ/канал недоступен — не блокируем людей
        if not ok and ch["request"]:
            ok = bool(await fetchone(
                "SELECT 1 FROM join_requests WHERE chat_id=? AND user_id=?", (ch["chat_id"], uid)))
        if not ok:
            missing.append(ch)
    return missing


def sub_kb(missing):
    rows = [[ub(txt("btn_channel", title=ch["title"]), ch["link"])] for ch in missing]
    rows.append([cb(txt("btn_check_sub"), "check_sub")])
    return ikb(*rows)


@router.chat_join_request()
async def on_join_request(r: ChatJoinRequest):
    await execute("INSERT OR IGNORE INTO join_requests(chat_id,user_id) VALUES(?,?)", (r.chat.id, r.from_user.id))


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(c: CallbackQuery, bot: Bot):
    SUB_OK.pop(c.from_user.id, None)
    if await missing_channels(bot, c.from_user.id):
        await c.answer(re.sub(r"<.*?>", "", txt("sub_fail")), show_alert=True)
        return
    SUB_OK[c.from_user.id] = time.time()
    try:
        await c.message.edit_text(txt("sub_ok"))
    except TelegramAPIError:
        pass
    await c.answer()


# ═════════════════════ ШЛЮЗ: бан / выключение / подписка ═════════════════════

async def gate_reply(event, text, markup=None):
    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.answer(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


class Gate(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)
        if isinstance(event, Message) and (event.successful_payment or event.chat.type != "private"):
            return await handler(event, data)
        await upsert_user(user)
        if await is_admin(user.id):
            return await handler(event, data)
        row = await fetchone("SELECT banned FROM users WHERE id=?", (user.id,))
        if row and row["banned"]:
            return await gate_reply(event, txt("banned"))
        if SETTINGS.get("sys:enabled", "1") != "1":
            return await gate_reply(event, txt("disabled"))
        is_check = isinstance(event, CallbackQuery) and event.data == "check_sub"
        if not is_check and time.time() - SUB_OK.get(user.id, 0) > 60:
            missing = await missing_channels(data["bot"], user.id)
            if missing:
                return await gate_reply(event, txt("sub_required"), sub_kb(missing))
            SUB_OK[user.id] = time.time()
        return await handler(event, data)


# ═════════════════════ ДОСТАВКА СООБЩЕНИЙ ═════════════════════

async def deliver(bot: Bot, sender_id: int, receiver_id: int, text: str, is_reply=False) -> bool:
    rcv = await fetchone("SELECT banned FROM users WHERE id=?", (receiver_id,))
    if not rcv or rcv["banned"]:
        return False
    msg_id = await execute(
        "INSERT INTO messages(sender_id, receiver_id, text, is_reply) VALUES(?,?,?,?)",
        (sender_id, receiver_id, text, int(is_reply)))
    title = txt("reply_title" if is_reply else "new_msg_title")
    kb = ikb([cb(txt("btn_reply"), f"reply:{msg_id}")],
             [cb(txt("btn_reveal", price=REVEAL_PRICE), f"reveal:{msg_id}")])
    try:
        await bot.send_message(receiver_id, f"{title}\n\n{html.escape(text)}", reply_markup=kb)
        return True
    except TelegramAPIError:
        return False


# ═════════════════════ ПОЛЬЗОВАТЕЛЬСКИЕ КОМАНДЫ ═════════════════════

@router.message(Command("claim"))
async def cmd_claim(m: Message, command: CommandObject, bot: Bot):
    key = os.getenv("ADMIN_KEY")
    if not key or not command.args or not hmac.compare_digest(command.args.strip(), key):
        return
    if await fetchone("SELECT 1 FROM admins"):
        await m.answer("Владелец уже назначен.")
        return
    await execute("INSERT INTO admins(id, is_owner) VALUES(?,1)", (m.from_user.id,))
    try:
        await m.delete()  # убираем сообщение с ключом
    except TelegramAPIError:
        pass
    await bot.send_message(m.from_user.id, "✅ Вы — владелец бота. Откройте панель: /admin")


@router.message(CommandStart())
async def cmd_start(m: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    code = (command.args or "").strip().lower()
    if code:
        target = await fetchone("SELECT id FROM users WHERE lower(code)=?", (code,))
        if not target:
            await m.answer(txt("link_invalid"))
            return
        if target["id"] == m.from_user.id:
            await m.answer(txt("own_link"))
            return
        await execute("UPDATE users SET visits = visits + 1 WHERE id=?", (target["id"],))
        await state.set_state(Writing.text)
        await state.update_data(target=target["id"])
        await m.answer(txt("write_prompt"), reply_markup=ikb(
            [cb(txt("btn_gift"), "gift_menu")], [cb(txt("btn_cancel"), "cancel")], [support_btn()]))
        return
    await m.answer(txt("start_new", link=await my_link(bot, m.from_user.id)), reply_markup=ikb([support_btn()]))


@router.message(Command("support"))
async def cmd_support(m: Message):
    await m.answer(txt("support_text"), reply_markup=ikb([support_btn()]))


@router.message(Command("mystats"))
async def cmd_stats(m: Message, bot: Bot):
    uid = m.from_user.id
    total = (await fetchone("SELECT COUNT(*) c FROM messages WHERE receiver_id=?", (uid,)))["c"]
    today = (await fetchone(
        "SELECT COUNT(*) c FROM messages WHERE receiver_id=? AND date(created_at)=date('now')", (uid,)))["c"]
    visits = (await fetchone("SELECT visits FROM users WHERE id=?", (uid,)))["visits"]
    await m.answer(txt("stats_text", today=today, total=total, visits=visits, link=await my_link(bot, uid)))


@router.message(Command("url"))
async def cmd_url(m: Message, command: CommandObject, bot: Bot):
    name = (command.args or "").strip()
    if not name:
        await m.answer(txt("url_info", link=await my_link(bot, m.from_user.id)))
        return
    if not re.fullmatch(r"[A-Za-z0-9_]{7,30}", name):
        await m.answer(txt("url_bad"))
        return
    name = name.lower()
    if await fetchone("SELECT id FROM users WHERE lower(code)=? AND id<>?", (name, m.from_user.id)):
        await m.answer(txt("url_taken"))
        return
    await execute("UPDATE users SET code=? WHERE id=?", (name, m.from_user.id))
    await m.answer(txt("url_done", link=await my_link(bot, m.from_user.id)))


@router.message(Command("issue"))
async def cmd_issue(m: Message, command: CommandObject, bot: Bot):
    if not command.args:
        await m.answer(txt("issue_help"))
        return
    for a in await fetchall("SELECT id FROM admins"):
        try:
            await bot.send_message(
                a["id"], f"💡 Идея от {m.from_user.id} (@{m.from_user.username}):\n{html.escape(command.args)}")
        except TelegramAPIError:
            pass
    await m.answer(txt("issue_thanks"))


# ───────── отправка сообщений и ответов ─────────

@router.callback_query(F.data == "cancel")
async def cb_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text(txt("cancelled"))
    except TelegramAPIError:
        pass
    await c.answer()


@router.message(Writing.text, F.text)
async def on_write(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    ok = await deliver(bot, m.from_user.id, data["target"], m.text)
    if ok:
        await m.answer(txt("sent_ok"))
    else:
        await m.answer(txt("deliver_fail"))
        await state.clear()


@router.callback_query(F.data.startswith("reply:"))
async def cb_reply(c: CallbackQuery, state: FSMContext):
    msg = await fetchone("SELECT * FROM messages WHERE id=?", (int(c.data.split(":")[1]),))
    if not msg or msg["receiver_id"] != c.from_user.id:
        await c.answer(re.sub(r"<.*?>", "", txt("msg_unavailable")), show_alert=True)
        return
    await state.set_state(Replying.text)
    await state.update_data(to=msg["sender_id"])
    await c.message.answer(txt("reply_prompt"))
    await c.answer()


@router.message(Replying.text, F.text)
async def on_reply(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    ok = await deliver(bot, m.from_user.id, data["to"], m.text, is_reply=True)
    await state.clear()
    await m.answer(txt("reply_sent") if ok else txt("reply_fail"))


# ───────── раскрытие отправителя ─────────

async def send_sender_info(bot: Bot, to_id: int, sender_id: int):
    u = await fetchone("SELECT * FROM users WHERE id=?", (sender_id,))
    name = html.escape(u["name"] or "—")
    uname = f"\n@{u['username']}" if u["username"] else ""
    await bot.send_message(
        to_id, txt("sender_info", name=f'<a href="tg://user?id={sender_id}">{name}</a>', username=uname))


@router.callback_query(F.data.startswith("reveal:"))
async def cb_reveal(c: CallbackQuery, bot: Bot):
    msg_id = int(c.data.split(":")[1])
    msg = await fetchone("SELECT * FROM messages WHERE id=?", (msg_id,))
    if not msg or msg["receiver_id"] != c.from_user.id:
        await c.answer(re.sub(r"<.*?>", "", txt("msg_unavailable")), show_alert=True)
        return
    if msg["revealed"]:
        await send_sender_info(bot, c.from_user.id, msg["sender_id"])
        await c.answer()
        return
    await bot.send_invoice(
        chat_id=c.from_user.id, title=txt("reveal_title")[:32], description=txt("reveal_desc")[:255],
        payload=f"reveal:{msg_id}", currency="XTR", provider_token="",
        prices=[LabeledPrice(label=txt("reveal_label")[:60], amount=REVEAL_PRICE)])
    await c.answer()


# ───────── подарки ─────────

@router.callback_query(F.data == "gift_menu")
async def cb_gift_menu(c: CallbackQuery, state: FSMContext, bot: Bot):
    if not (await state.get_data()).get("target"):
        await c.answer(re.sub(r"<.*?>", "", txt("link_invalid")), show_alert=True)
        return
    gifts = [g for g in (await bot.get_available_gifts()).gifts if g.remaining_count is None]
    gifts = sorted(gifts, key=lambda g: g.star_count)[:MAX_GIFTS_SHOWN]
    if not gifts:
        await c.answer(re.sub(r"<.*?>", "", txt("gifts_none")), show_alert=True)
        return
    b = InlineKeyboardBuilder()
    for g in gifts:
        b.button(text=f"{g.sticker.emoji or '🎁'} {g.star_count}⭐ → {g.star_count * GIFT_MULTIPLIER}⭐",
                 callback_data=f"g:{g.id}")
    b.button(text=txt("btn_cancel"), callback_data="cancel")
    b.adjust(2)
    await c.message.answer(txt("gifts_title"), reply_markup=b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("g:"))
async def cb_gift_pick(c: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if not data.get("target"):
        await c.answer(re.sub(r"<.*?>", "", txt("link_invalid")), show_alert=True)
        return
    gift_id = c.data.split(":", 1)[1]
    gift = next((g for g in (await bot.get_available_gifts()).gifts if g.id == gift_id), None)
    if not gift:
        await c.answer(re.sub(r"<.*?>", "", txt("gift_unavailable")), show_alert=True)
        return
    await state.set_state(GiftText.text)
    await state.update_data(gift_id=gift_id, gift_cost=gift.star_count)
    await c.message.answer(txt("gift_prompt"))
    await c.answer()


@router.message(GiftText.text, F.text)
async def on_gift_text(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    text = m.text[:100]
    price = data["gift_cost"] * GIFT_MULTIPLIER
    gid = await execute(
        "INSERT INTO gifts(sender_id, target_id, gift_id, price, cost, text) VALUES(?,?,?,?,?,?)",
        (m.from_user.id, data["target"], data["gift_id"], price, data["gift_cost"], text))
    await state.clear()
    await bot.send_invoice(
        chat_id=m.from_user.id, title=txt("gift_title")[:32],
        description=txt("gift_desc", text=text)[:255], payload=f"gift:{gid}",
        currency="XTR", provider_token="",
        prices=[LabeledPrice(label=txt("gift_label")[:60], amount=price)])


# ───────── оплата ─────────

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(m: Message, bot: Bot):
    pay = m.successful_payment
    kind, _, ref = pay.invoice_payload.partition(":")

    if kind == "reveal":
        msg = await fetchone("SELECT * FROM messages WHERE id=?", (int(ref),))
        await execute("UPDATE messages SET revealed=1 WHERE id=?", (int(ref),))
        await execute("INSERT INTO payments(kind,user_id,amount,profit) VALUES('reveal',?,?,?)",
                      (m.from_user.id, pay.total_amount, pay.total_amount))
        await send_sender_info(bot, m.from_user.id, msg["sender_id"])

    elif kind == "gift":
        g = await fetchone("SELECT * FROM gifts WHERE id=?", (int(ref),))
        if not g or g["paid"]:
            return
        try:
            await bot.send_gift(user_id=g["target_id"], gift_id=g["gift_id"],
                                text=txt("gift_prefix", text=g["text"])[:128])
            await execute("UPDATE gifts SET paid=1 WHERE id=?", (g["id"],))
            await execute("INSERT INTO payments(kind,user_id,amount,profit) VALUES('gift',?,?,?)",
                          (m.from_user.id, g["price"], g["price"] - (g["cost"] or 0)))
            await m.answer(txt("gift_sent"))
            try:
                await bot.send_message(g["target_id"], txt("gift_received"))
            except TelegramAPIError:
                pass
        except TelegramAPIError as e:
            logging.exception("send_gift failed: %s", e)
            await bot.refund_star_payment(user_id=m.from_user.id,
                                          telegram_payment_charge_id=pay.telegram_payment_charge_id)
            await m.answer(txt("gift_failed"))


# ═════════════════════ АДМИН-ПАНЕЛЬ ═════════════════════
# Все хендлеры ниже доступны только тем, кто есть в таблице admins.

class IsAdmin(BaseFilter):
    async def __call__(self, event) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u) and await is_admin(u.id)


adm.message.filter(IsAdmin())
adm.callback_query.filter(IsAdmin())

BG_TASKS: set = set()


async def show(target, text, markup=None):
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            pass
        await target.answer()
    else:
        await target.answer(text, reply_markup=markup)


def home_kb():
    enabled = SETTINGS.get("sys:enabled", "1") == "1"
    return ikb(
        [cb("📊 Статистика", "a:stats"), cb("📣 Рассылка", "a:bc")],
        [cb("📢 Обязательная подписка", "a:sub")],
        [cb("👮 Админы", "a:adm"), cb("🚫 Блокировки", "a:ban")],
        [cb("🔗 Ссылки пользователей", "a:links")],
        [cb("🎨 Кастомизация", "a:cz:0")],
        [cb("🟢 Бот включён — выключить" if enabled else "🔴 Бот выключен — включить", "a:toggle")],
        [cb("✖️ Закрыть", "a:close")],
    )


BACK = ikb([cb("🔙 В панель", "a:home")])


@adm.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("🛠 <b>Админ-панель</b>", reply_markup=home_kb())


@adm.callback_query(F.data == "a:home")
async def a_home(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "🛠 <b>Админ-панель</b>", home_kb())


@adm.callback_query(F.data == "a:cancel")
async def a_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "🛠 <b>Админ-панель</b>", home_kb())


@adm.callback_query(F.data == "a:close")
async def a_close(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.delete()
    except TelegramAPIError:
        pass
    await c.answer()


# ───────── статистика ─────────

@adm.callback_query(F.data == "a:stats")
async def a_stats(c: CallbackQuery):
    one = lambda sql, a=(): fetchone(sql, a)
    users = (await one("SELECT COUNT(*) c FROM users"))["c"]
    today = (await one("SELECT COUNT(*) c FROM users WHERE date(joined_at)=date('now')"))["c"]
    banned = (await one("SELECT COUNT(*) c FROM users WHERE banned=1"))["c"]
    admins = (await one("SELECT COUNT(*) c FROM admins"))["c"]
    msgs = (await one("SELECT COUNT(*) c FROM messages WHERE is_reply=0"))["c"]
    reps = (await one("SELECT COUNT(*) c FROM messages WHERE is_reply=1"))["c"]
    msgs_today = (await one("SELECT COUNT(*) c FROM messages WHERE date(created_at)=date('now')"))["c"]
    rv = await one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s FROM payments WHERE kind='reveal'")
    gf = await one("SELECT COUNT(*) c, COALESCE(SUM(amount),0) s, COALESCE(SUM(profit),0) p FROM payments WHERE kind='gift'")
    chans = (await one("SELECT COUNT(*) c FROM channels"))["c"]
    total_stars = rv["s"] + gf["s"]
    total_profit = rv["s"] + gf["p"]
    await show(c,
        "📊 <b>Статистика бота за всё время</b>\n\n"
        f"👥 Пользователей: <b>{users}</b> (сегодня +{today})\n"
        f"🚫 Заблокировано: {banned}\n👮 Админов: {admins}\n📢 Каналов подписки: {chans}\n\n"
        f"✉️ Сообщений: <b>{msgs}</b> (сегодня: {msgs_today})\n💬 Ответов: {reps}\n\n"
        f"👁 Раскрытий: {rv['c']} → {rv['s']} ⭐\n"
        f"🎁 Подарков: {gf['c']} → {gf['s']} ⭐ (прибыль {gf['p']} ⭐)\n\n"
        f"💰 Оборот: <b>{total_stars} ⭐</b>\n💎 Чистая прибыль: <b>{total_profit} ⭐</b>\n"
        f"🔌 Статус: {'включён' if SETTINGS.get('sys:enabled', '1') == '1' else 'выключен'}",
        BACK)


# ───────── вкл/выкл бот ─────────

@adm.callback_query(F.data == "a:toggle")
async def a_toggle(c: CallbackQuery):
    new = "0" if SETTINGS.get("sys:enabled", "1") == "1" else "1"
    await set_setting("sys:enabled", new)
    await show(c, "🛠 <b>Админ-панель</b>", home_kb())


# ───────── рассылка ─────────

@adm.callback_query(F.data == "a:bc")
async def a_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.broadcast)
    await show(c, "📣 Отправьте сообщение для рассылки — текст, фото, видео, файл (с форматированием и кнопками-ссылками).",
               ikb([cb("❌ Отмена", "a:cancel")]))


@adm.message(Adm.broadcast, NotCommand())
async def a_bc_msg(m: Message, state: FSMContext, bot: Bot):
    await state.update_data(src_chat=m.chat.id, src_msg=m.message_id)
    n = (await fetchone("SELECT COUNT(*) c FROM users WHERE banned=0"))["c"]
    await bot.copy_message(m.chat.id, m.chat.id, m.message_id)
    await m.answer(f"👆 Так будет выглядеть рассылка.\nПолучателей: <b>{n}</b>",
                   reply_markup=ikb([cb("✅ Отправить", "a:bcgo")], [cb("❌ Отмена", "a:cancel")]))


async def run_broadcast(bot: Bot, admin_id: int, src_chat: int, src_msg: int):
    ok = fail = 0
    for r in await fetchall("SELECT id FROM users WHERE banned=0"):
        try:
            await bot.copy_message(r["id"], src_chat, src_msg)
            ok += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(r["id"], src_chat, src_msg)
                ok += 1
            except TelegramAPIError:
                fail += 1
        except TelegramAPIError:
            fail += 1
        await asyncio.sleep(0.05)
    await bot.send_message(admin_id, f"📣 <b>Рассылка завершена</b>\n✅ Доставлено: {ok}\n❌ Не доставлено: {fail}")


@adm.callback_query(F.data == "a:bcgo")
async def a_bc_go(c: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    await state.clear()
    if "src_msg" not in d:
        await c.answer("Нет сообщения", show_alert=True)
        return
    t = asyncio.create_task(run_broadcast(bot, c.from_user.id, d["src_chat"], d["src_msg"]))
    BG_TASKS.add(t)
    t.add_done_callback(BG_TASKS.discard)
    await show(c, "🚀 Рассылка запущена. Отчёт придёт по завершении.", BACK)


# ───────── обязательная подписка ─────────

async def sub_menu(target):
    rows = []
    for ch in await fetchall("SELECT * FROM channels"):
        mode = "заявки" if ch["request"] else "обычная"
        rows.append([cb(f"🗑 {ch['title']} ({mode})", f"a:subdel:{ch['id']}")])
    rows.append([cb("➕ Добавить канал", "a:subadd")])
    rows.append([cb("🔙 В панель", "a:home")])
    await show(target, "📢 <b>Обязательная подписка</b>\nНажмите на канал, чтобы удалить.", ikb(*rows))


@adm.callback_query(F.data == "a:sub")
async def a_sub(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await sub_menu(c)


@adm.callback_query(F.data.startswith("a:subdel:"))
async def a_sub_del(c: CallbackQuery):
    await execute("DELETE FROM channels WHERE id=?", (int(c.data.split(":")[2]),))
    SUB_OK.clear()
    await sub_menu(c)


@adm.callback_query(F.data == "a:subadd")
async def a_sub_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.sub_chat)
    await show(c,
        "➕ Добавьте бота <b>администратором</b> в канал (с правом «Приглашать пользователей»), затем отправьте:\n"
        "• ID канала (<code>-100…</code>), или\n• @username, или\n• <b>перешлите любой пост</b> из канала.\n\n"
        "Работает и для приватных каналов без username, и для каналов с заявками.",
        ikb([cb("❌ Отмена", "a:cancel")]))


@adm.message(Adm.sub_chat, NotCommand())
async def a_sub_chat(m: Message, state: FSMContext, bot: Bot):
    ref = None
    origin = getattr(m, "forward_origin", None)
    if origin is not None and getattr(origin, "chat", None):
        ref = origin.chat.id
    elif m.text:
        t = m.text.strip()
        if re.fullmatch(r"-?\d+", t):
            ref = int(t)
        else:
            mm = re.search(r"(?:t\.me/|@)([A-Za-z0-9_]{4,})", t)
            ref = "@" + mm.group(1) if mm else None
    if ref is None:
        await m.answer("❌ Не понял. Отправьте ID, @username или перешлите пост из канала.")
        return
    try:
        chat = await bot.get_chat(ref)
        me = await bot.me()
        mem = await bot.get_chat_member(chat.id, me.id)
        if mem.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            raise ValueError("бот не админ")
    except (TelegramAPIError, ValueError) as e:
        await m.answer(f"❌ Не удалось: {html.escape(str(e))}\nПроверьте, что бот — админ канала.")
        return
    await state.update_data(chat_id=chat.id, title=chat.title or str(chat.id))
    await m.answer(f"Канал: <b>{html.escape(chat.title or '')}</b>\nВыберите тип подписки:",
                   reply_markup=ikb([cb("🌐 Обычная подписка", "a:submode:0")],
                                    [cb("📝 С заявками на вступление", "a:submode:1")],
                                    [cb("❌ Отмена", "a:cancel")]))


@adm.callback_query(F.data.startswith("a:submode:"))
async def a_sub_mode(c: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if "chat_id" not in d:
        await c.answer("Сначала выберите канал", show_alert=True)
        return
    req = int(c.data.split(":")[2])
    try:
        link = await bot.create_chat_invite_link(d["chat_id"], name="OrenixAnon", creates_join_request=bool(req))
    except TelegramAPIError as e:
        await c.answer(f"Не удалось создать ссылку: {e}", show_alert=True)
        return
    await execute(
        "INSERT INTO channels(chat_id,title,link,request) VALUES(?,?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=excluded.link, request=excluded.request",
        (d["chat_id"], d["title"], link.invite_link, req))
    SUB_OK.clear()
    await state.clear()
    await sub_menu(c)


# ───────── админы ─────────

async def adm_menu(target, uid):
    rows = []
    owner = await is_owner(uid)
    for a in await fetchall("SELECT a.id, a.is_owner, u.name FROM admins a LEFT JOIN users u ON u.id=a.id"):
        label = f"{'👑' if a['is_owner'] else '👮'} {a['name'] or ''} ({a['id']})"
        if a["is_owner"] or not owner:
            rows.append([cb(label, "a:noop")])
        else:
            rows.append([cb(f"🗑 {label}", f"a:admdel:{a['id']}")])
    if owner:
        rows.append([cb("➕ Назначить админа", "a:admadd")])
    rows.append([cb("🔙 В панель", "a:home")])
    await show(target, "👮 <b>Админы</b>" + ("" if owner else "\n(назначать и снимать может только владелец)"), ikb(*rows))


@adm.callback_query(F.data == "a:noop")
async def a_noop(c: CallbackQuery):
    await c.answer()


@adm.callback_query(F.data == "a:adm")
async def a_adm(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await adm_menu(c, c.from_user.id)


@adm.callback_query(F.data == "a:admadd")
async def a_adm_add(c: CallbackQuery, state: FSMContext):
    if not await is_owner(c.from_user.id):
        await c.answer("Только владелец", show_alert=True)
        return
    await state.set_state(Adm.add_admin)
    await show(c, "Отправьте ID или @username пользователя (он должен был запускать бота):",
               ikb([cb("❌ Отмена", "a:cancel")]))


@adm.message(Adm.add_admin, NotCommand())
async def a_adm_add_msg(m: Message, state: FSMContext, bot: Bot):
    if not await is_owner(m.from_user.id):
        return
    u = await resolve_user(m.text or "")
    if not u:
        await m.answer("❌ Пользователь не найден. Он должен запустить бота.")
        return
    await execute("INSERT OR IGNORE INTO admins(id, is_owner, added_by) VALUES(?,0,?)", (u["id"], m.from_user.id))
    await state.clear()
    try:
        await bot.send_message(u["id"], "👮 Вас назначили администратором. Откройте /admin")
    except TelegramAPIError:
        pass
    await m.answer(f"✅ {html.escape(u['name'] or '')} ({u['id']}) теперь админ.", reply_markup=BACK)


@adm.callback_query(F.data.startswith("a:admdel:"))
async def a_adm_del(c: CallbackQuery, bot: Bot):
    if not await is_owner(c.from_user.id):
        await c.answer("Только владелец", show_alert=True)
        return
    uid = int(c.data.split(":")[2])
    await execute("DELETE FROM admins WHERE id=? AND is_owner=0", (uid,))
    try:
        await bot.send_message(uid, "Вы больше не администратор.")
    except TelegramAPIError:
        pass
    await adm_menu(c, c.from_user.id)


# ───────── блокировки ─────────

@adm.callback_query(F.data == "a:ban")
async def a_ban(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "🚫 <b>Блокировки</b>", ikb(
        [cb("🚫 Заблокировать", "a:banadd"), cb("✅ Разблокировать", "a:unban")],
        [cb("📋 Список заблокированных", "a:banlist")],
        [cb("🔙 В панель", "a:home")]))


@adm.callback_query(F.data == "a:banadd")
async def a_ban_add(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.ban)
    await show(c, "Отправьте ID, @username или ссылку пользователя:", ikb([cb("❌ Отмена", "a:cancel")]))


@adm.callback_query(F.data == "a:unban")
async def a_unban(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.unban)
    await show(c, "Отправьте ID, @username или ссылку пользователя:", ikb([cb("❌ Отмена", "a:cancel")]))


async def _ban_common(m: Message, state: FSMContext, value: int):
    u = await resolve_user(m.text or "")
    if not u:
        await m.answer("❌ Пользователь не найден.")
        return
    if value and await is_admin(u["id"]):
        await m.answer("❌ Нельзя заблокировать админа.")
        return
    await execute("UPDATE users SET banned=? WHERE id=?", (value, u["id"]))
    await state.clear()
    await m.answer(f"{'🚫 Заблокирован' if value else '✅ Разблокирован'}: "
                   f"{html.escape(u['name'] or '')} ({u['id']})", reply_markup=BACK)


@adm.message(Adm.ban, NotCommand())
async def a_ban_msg(m: Message, state: FSMContext):
    await _ban_common(m, state, 1)


@adm.message(Adm.unban, NotCommand())
async def a_unban_msg(m: Message, state: FSMContext):
    await _ban_common(m, state, 0)


@adm.callback_query(F.data == "a:banlist")
async def a_ban_list(c: CallbackQuery):
    rows = await fetchall("SELECT id, name, username FROM users WHERE banned=1 LIMIT 50")
    body = "\n".join(f"• {html.escape(r['name'] or '')} @{r['username'] or '—'} <code>{r['id']}</code>" for r in rows)
    await show(c, "📋 <b>Заблокированные</b>\n\n" + (body or "Список пуст."), ikb([cb("🔙 Назад", "a:ban")]))


# ───────── ссылки пользователей ─────────

@adm.callback_query(F.data == "a:links")
async def a_links(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c, "🔗 <b>Ссылки пользователей</b>", ikb(
        [cb("♻️ Аннулировать ссылку", "a:annul")],
        [cb("✏️ Изменить ссылку пользователя", "a:relink")],
        [cb("🔙 В панель", "a:home")]))


@adm.callback_query(F.data == "a:annul")
async def a_annul(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.annul)
    await show(c, "Отправьте ID, @username, код или ссылку пользователя.\n"
                  "Его текущая ссылка (в т.ч. заданная через /url) перестанет работать, ему выдастся случайная новая.",
               ikb([cb("❌ Отмена", "a:cancel")]))


@adm.message(Adm.annul, NotCommand())
async def a_annul_msg(m: Message, state: FSMContext, bot: Bot):
    u = await resolve_user(m.text or "")
    if not u:
        await m.answer("❌ Пользователь не найден.")
        return
    code = await unique_code()
    await execute("UPDATE users SET code=? WHERE id=?", (code, u["id"]))
    await state.clear()
    try:
        await bot.send_message(u["id"], txt("link_annulled", link=await link_for_code(bot, code)))
    except TelegramAPIError:
        pass
    await m.answer(f"♻️ Ссылка {html.escape(u['name'] or '')} ({u['id']}) аннулирована.\n"
                   f"Новая: <code>{await link_for_code(bot, code)}</code>", reply_markup=BACK)


@adm.callback_query(F.data == "a:relink")
async def a_relink(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.relink)
    await show(c, "Отправьте: <code>ID_или_@username НовыйКод</code>\nНапример: <code>123456789 VipUser</code>\n"
                  "(3–30 символов: латиница, цифры, _)", ikb([cb("❌ Отмена", "a:cancel")]))


@adm.message(Adm.relink, NotCommand())
async def a_relink_msg(m: Message, state: FSMContext, bot: Bot):
    parts = (m.text or "").split()
    if len(parts) != 2 or not re.fullmatch(r"[A-Za-z0-9_]{3,30}", parts[1]):
        await m.answer("❌ Формат: <code>ID НовыйКод</code> (латиница, цифры, _, 3–30 символов).")
        return
    u = await resolve_user(parts[0])
    if not u:
        await m.answer("❌ Пользователь не найден.")
        return
    code = parts[1].lower()
    if await fetchone("SELECT id FROM users WHERE lower(code)=? AND id<>?", (code, u["id"])):
        await m.answer("❌ Этот код уже занят.")
        return
    await execute("UPDATE users SET code=? WHERE id=?", (code, u["id"]))
    await state.clear()
    try:
        await bot.send_message(u["id"], txt("link_changed", link=await link_for_code(bot, code)))
    except TelegramAPIError:
        pass
    await m.answer(f"✅ Новая ссылка {html.escape(u['name'] or '')}: <code>{await link_for_code(bot, code)}</code>",
                   reply_markup=BACK)


# ───────── кастомизация текстов и кнопок ─────────

KEYS = list(DEFAULTS)
PER_PAGE = 8


@adm.callback_query(F.data.startswith("a:cz:"))
async def a_cz(c: CallbackQuery, state: FSMContext):
    await state.clear()
    page = int(c.data.split(":")[2])
    chunk = KEYS[page * PER_PAGE:(page + 1) * PER_PAGE]
    rows = [[cb(("✏️ " if k in SETTINGS else "") + DEFAULTS[k][0], f"a:ck:{KEYS.index(k)}")] for k in chunk]
    nav = []
    if page > 0:
        nav.append(cb("⬅️", f"a:cz:{page - 1}"))
    if (page + 1) * PER_PAGE < len(KEYS):
        nav.append(cb("➡️", f"a:cz:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([cb("🔙 В панель", "a:home")])
    await show(c, f"🎨 <b>Кастомизация</b> — стр. {page + 1}/{(len(KEYS) - 1) // PER_PAGE + 1}\n"
                  "✏️ — значение изменено. Выберите текст или кнопку:", ikb(*rows))


async def cz_view(target, idx: int):
    key = KEYS[idx]
    label, default, vars_, kind = DEFAULTS[key]
    cur = SETTINGS.get(key, default)
    text = (f"🎨 <b>{html.escape(label)}</b>\n"
            f"Переменные: <code>{html.escape(vars_) if vars_ else 'нет'}</code>\n"
            f"Формат: {'HTML (можно форматировать)' if kind == 'html' else 'обычный текст'}\n\n"
            f"<b>Сейчас:</b>\n<pre>{html.escape(cur)[:3000]}</pre>")
    await show(target, text, ikb(
        [cb("✏️ Изменить", f"a:ce:{idx}"), cb("♻️ Сбросить", f"a:cr:{idx}")],
        [cb("🔙 К списку", f"a:cz:{idx // PER_PAGE}")]))


@adm.callback_query(F.data.startswith("a:ck:"))
async def a_ck(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await cz_view(c, int(c.data.split(":")[2]))


@adm.callback_query(F.data.startswith("a:ce:"))
async def a_ce(c: CallbackQuery, state: FSMContext):
    idx = int(c.data.split(":")[2])
    await state.set_state(Adm.edit_text)
    await state.update_data(idx=idx)
    label, _, vars_, kind = DEFAULTS[KEYS[idx]]
    await show(c, f"✍️ Отправьте новое значение для «{html.escape(label)}».\n"
                  f"Переменные: <code>{html.escape(vars_) if vars_ else 'нет'}</code>\n"
                  + ("Можно использовать форматирование Telegram (жирный, ссылки, цитаты)." if kind == "html" else ""),
               ikb([cb("❌ Отмена", f"a:ck:{idx}")]))


@adm.message(Adm.edit_text, NotCommand())
async def a_edit_save(m: Message, state: FSMContext, bot: Bot):
    if not m.text:
        await m.answer("Нужен текст.")
        return
    d = await state.get_data()
    idx = d["idx"]
    key = KEYS[idx]
    kind = DEFAULTS[key][3]
    value = m.html_text if kind == "html" else m.text
    if key == "support_url":
        v = value.strip()
        if v.startswith("@"):
            v = "https://t.me/" + v[1:]
        if not re.fullmatch(r"https?://\S+", v):
            await m.answer("❌ Нужна ссылка вида https://t.me/name или @name")
            return
        value = v
    elif kind == "html":
        try:
            prev = await bot.send_message(m.chat.id, value)
            await prev.delete()
        except TelegramBadRequest as e:
            await m.answer(f"❌ Ошибка разметки: {html.escape(str(e))}")
            return
    await set_setting(key, value)
    await state.clear()
    await m.answer("✅ Сохранено.")
    await cz_view(m, idx)


@adm.callback_query(F.data.startswith("a:cr:"))
async def a_cr(c: CallbackQuery):
    idx = int(c.data.split(":")[2])
    await del_setting(KEYS[idx])
    await cz_view(c, idx)


# ═════════════════════ ЗАПУСК ═════════════════════

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    if not os.getenv("ADMIN_KEY") and not await fetchone("SELECT 1 FROM admins"):
        logging.warning("ADMIN_KEY не задан и админов нет — назначить владельца будет невозможно!")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(Gate())
    dp.callback_query.outer_middleware(Gate())
    dp.include_router(adm)      # админка раньше пользовательских хендлеров
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="запуск"),
        BotCommand(command="mystats", description="статистика"),
        BotCommand(command="url", description="изменить ссылку"),
        BotCommand(command="issue", description="предложить идею"),
        BotCommand(command="support", description="поддержка"),
    ])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
