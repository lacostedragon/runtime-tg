"""
RunTime Visuals — Telegram Bot
"""

import asyncio
import time
import logging
import os
import json
from datetime import datetime, timezone, timedelta
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove
)
import aiosqlite

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN     = os.environ["BOT_TOKEN"]
API_PORT      = int(os.environ.get("PORT", 8080))
CHANNEL_ID    = os.environ.get("CHANNEL_ID", "@runtime_visuals")   # @username или -100xxx
ADMIN_GROUP   = int(os.environ.get("ADMIN_GROUP_ID", "-1003709336541"))
TIMEOUT_SEC   = 90
DB_PATH       = "runtime.db"
MSK           = timezone(timedelta(hours=3))
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())

# uuid -> last_seen
clients: dict[str, float] = {}


class SupportState(StatesGroup):
    waiting_support = State()
    waiting_bugreport = State()


# ─── DATABASE ──────────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                referred_by INTEGER,
                joined_at   TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS peak_online (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                peak_all    INTEGER DEFAULT 0,
                peak_all_at TEXT,
                peak_day    INTEGER DEFAULT 0,
                peak_day_at TEXT,
                peak_day_date TEXT
            )
        """)
        await db.execute("""
            INSERT OR IGNORE INTO peak_online (id, peak_all, peak_day, peak_day_date)
            VALUES (1, 0, 0, '')
        """)
        # support_map: msg_id в группе -> user_id (для reply)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_map (
                group_msg_id INTEGER PRIMARY KEY,
                user_id      INTEGER,
                type         TEXT
            )
        """)
        await db.commit()


async def get_or_create_user(user_id: int, username: str | None, referred_by: int | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, username, referred_by, joined_at) VALUES (?, ?, ?, ?)",
                (user_id, username, referred_by, datetime.now(MSK).isoformat())
            )
            await db.commit()
            return True  # новый
        else:
            await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            await db.commit()
            return False  # уже был


async def get_referral_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, username, referred_by, joined_at FROM users WHERE user_id = ?", (user_id,)) as cur:
            return await cur.fetchone()


async def update_peaks(current: int):
    now = datetime.now(MSK)
    today = now.strftime("%Y-%m-%d")
    now_str = now.strftime("%d.%m.%Y %H:%M МСК")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT peak_all, peak_all_at, peak_day, peak_day_at, peak_day_date FROM peak_online WHERE id = 1") as cur:
            row = await cur.fetchone()
        peak_all, peak_all_at, peak_day, peak_day_at, peak_day_date = row

        if peak_day_date != today:
            peak_day = 0
            peak_day_at = None

        changed = False
        if current > peak_all:
            peak_all = current
            peak_all_at = now_str
            changed = True
        if current > peak_day:
            peak_day = current
            peak_day_at = now_str
            changed = True

        if changed or peak_day_date != today:
            await db.execute(
                "UPDATE peak_online SET peak_all=?, peak_all_at=?, peak_day=?, peak_day_at=?, peak_day_date=? WHERE id=1",
                (peak_all, peak_all_at, peak_day, peak_day_at, today)
            )
            await db.commit()


async def get_peaks():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT peak_all, peak_all_at, peak_day, peak_day_at FROM peak_online WHERE id=1") as cur:
            return await cur.fetchone()


async def save_support_map(group_msg_id: int, user_id: int, type_: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO support_map (group_msg_id, user_id, type) VALUES (?, ?, ?)",
            (group_msg_id, user_id, type_)
        )
        await db.commit()


async def get_support_map(group_msg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, type FROM support_map WHERE group_msg_id = ?", (group_msg_id,)) as cur:
            return await cur.fetchone()


# ─── HELPERS ───────────────────────────────────────────────────────────────────

def get_online() -> int:
    now = time.time()
    return sum(1 for t in clients.values() if now - t < TIMEOUT_SEC)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🟢 Онлайн")],
            [KeyboardButton(text="🛠 Тех. поддержка"), KeyboardButton(text="🐛 Баг-репорт")],
        ],
        resize_keyboard=True
    )


def subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{CHANNEL_ID.lstrip('@')}")],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")],
    ])


async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return False


async def require_sub(message: types.Message) -> bool:
    """Возвращает True если подписан, иначе отправляет сообщение с кнопками и возвращает False."""
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "📢 <b>Для использования бота необходимо подписаться на наш канал!</b>",
            parse_mode="HTML",
            reply_markup=subscribe_keyboard()
        )
        return False
    return True


async def require_sub_callback(callback: types.CallbackQuery) -> bool:
    if not await is_subscribed(callback.from_user.id):
        await callback.message.edit_text(
            "📢 <b>Для использования бота необходимо подписаться на наш канал!</b>",
            parse_mode="HTML",
            reply_markup=subscribe_keyboard()
        )
        return False
    return True


# ─── HANDLERS ──────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user = message.from_user
    args = message.text.split()
    referred_by = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referred_by = int(args[1][4:])
            if referred_by == user.id:
                referred_by = None
        except ValueError:
            pass

    await get_or_create_user(user.id, user.username, referred_by)

    if not await is_subscribed(user.id):
        await message.answer(
            "👋 <b>Добро пожаловать в RunTime Visuals!</b>\n\n"
            "📢 Для использования бота необходимо подписаться на наш канал.",
            parse_mode="HTML",
            reply_markup=subscribe_keyboard()
        )
        return

    await message.answer(
        "👋 <b>Добро пожаловать в RunTime Visuals!</b>\n\n"
        "Выбери раздел в меню ниже 👇",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    if await is_subscribed(callback.from_user.id):
        await callback.message.edit_text(
            "✅ <b>Подписка подтверждена!</b>",
            parse_mode="HTML"
        )
        await callback.message.answer(
            "Выбери раздел в меню ниже 👇",
            reply_markup=main_keyboard()
        )
    else:
        await callback.message.edit_text(
            "❌ <b>Ты ещё не подписался на канал!</b>\n\nПодпишись и нажми «Проверить подписку».",
            parse_mode="HTML",
            reply_markup=subscribe_keyboard()
        )


@dp.message(F.text == "👤 Профиль")
async def profile_handler(message: types.Message):
    if not await require_sub(message):
        return
    user = message.from_user
    await get_or_create_user(user.id, user.username)
    row = await get_user(user.id)
    ref_count = await get_referral_count(user.id)
    username = f"@{row[1]}" if row[1] else "Unknown"
    ref_link = f"https://t.me/{(await bot.get_me()).username}?start=ref_{user.id}"
    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👤 Username: {username}\n"
        f"👥 Приглашено: <b>{ref_count}</b> чел.\n\n"
        f"🔗 Реферальная ссылка:\n<code>{ref_link}</code>",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@dp.message(F.text == "🟢 Онлайн")
async def online_handler(message: types.Message):
    if not await require_sub(message):
        return
    current = get_online()
    peaks = await get_peaks()
    peak_all, peak_all_at, peak_day, peak_day_at = peaks
    now_str = datetime.now(MSK).strftime("%d.%m.%Y %H:%M МСК")
    await message.answer(
        f"🟢 <b>Онлайн RunTime Visuals</b>\n\n"
        f"👥 Сейчас онлайн: <b>{current}</b>\n"
        f"🕐 Время: {now_str}\n\n"
        f"📅 Пик за сегодня: <b>{peak_day}</b>\n"
        f"🕐 {peak_day_at or '—'}\n\n"
        f"🏆 Пик за всё время: <b>{peak_all}</b>\n"
        f"🕐 {peak_all_at or '—'}",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@dp.message(F.text == "🛠 Тех. поддержка")
async def support_handler(message: types.Message, state: FSMContext):
    if not await require_sub(message):
        return
    await state.set_state(SupportState.waiting_support)
    await message.answer(
        "🛠 <b>Тех. поддержка</b>\n\n"
        "Опиши свою проблему. Можешь прикрепить фото или видео.\n\n"
        "Для отмены напиши /cancel",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(F.text == "🐛 Баг-репорт")
async def bugreport_handler(message: types.Message, state: FSMContext):
    if not await require_sub(message):
        return
    await state.set_state(SupportState.waiting_bugreport)
    await message.answer(
        "🐛 <b>Баг-репорт</b>\n\n"
        "Опиши баг подробно. Можешь прикрепить фото или видео.\n\n"
        "Для отмены напиши /cancel",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(Command("cancel"))
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=main_keyboard())


async def forward_to_admins(message: types.Message, type_: str):
    user = message.from_user
    username = f"@{user.username}" if user.username else "нет"
    label = "🛠 Тех. поддержка" if type_ == "support" else "🐛 Баг-репорт"
    header = (
        f"{label}\n"
        f"👤 {user.full_name} | {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"─────────────────\n"
    )

    if message.text:
        sent = await bot.send_message(ADMIN_GROUP, header + message.text, parse_mode="HTML")
    elif message.photo:
        sent = await bot.send_photo(ADMIN_GROUP, message.photo[-1].file_id, caption=header + (message.caption or ""), parse_mode="HTML")
    elif message.video:
        sent = await bot.send_video(ADMIN_GROUP, message.video.file_id, caption=header + (message.caption or ""), parse_mode="HTML")
    elif message.document:
        sent = await bot.send_document(ADMIN_GROUP, message.document.file_id, caption=header + (message.caption or ""), parse_mode="HTML")
    else:
        sent = await bot.send_message(ADMIN_GROUP, header + "[неподдерживаемый тип сообщения]", parse_mode="HTML")

    await save_support_map(sent.message_id, user.id, type_)
    return sent


@dp.message(SupportState.waiting_support)
async def support_message(message: types.Message, state: FSMContext):
    await state.clear()
    await forward_to_admins(message, "support")
    await message.answer(
        "✅ <b>Сообщение отправлено в тех. поддержку!</b>\n\nОжидай ответа от администратора.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@dp.message(SupportState.waiting_bugreport)
async def bugreport_message(message: types.Message, state: FSMContext):
    await state.clear()
    await forward_to_admins(message, "bugreport")
    await message.answer(
        "✅ <b>Баг-репорт отправлен!</b>\n\nСпасибо, мы разберёмся.",
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


@dp.message(F.chat.id == ADMIN_GROUP, F.reply_to_message)
async def admin_reply(message: types.Message):
    """Когда админ отвечает на сообщение в группе — пересылаем юзеру."""
    replied_id = message.reply_to_message.message_id
    row = await get_support_map(replied_id)
    if not row:
        return
    user_id, type_ = row
    label = "🛠 Тех. поддержка" if type_ == "support" else "🐛 Баг-репорт"
    admin = message.from_user
    admin_name = f"@{admin.username}" if admin.username else admin.full_name
    header = f"📩 <b>Ответ от администратора</b> ({label})\n👤 {admin_name}\n─────────────────\n"

    try:
        if message.text:
            await bot.send_message(user_id, header + message.text, parse_mode="HTML")
        elif message.photo:
            await bot.send_photo(user_id, message.photo[-1].file_id, caption=header + (message.caption or ""), parse_mode="HTML")
        elif message.video:
            await bot.send_video(user_id, message.video.file_id, caption=header + (message.caption or ""), parse_mode="HTML")
        elif message.document:
            await bot.send_document(user_id, message.document.file_id, caption=header + (message.caption or ""), parse_mode="HTML")
        await message.react([types.ReactionTypeEmoji(emoji="✅")])
    except Exception as e:
        log.warning(f"Не удалось отправить ответ юзеру {user_id}: {e}")
        await message.reply(f"❌ Не удалось доставить сообщение пользователю (ID: {user_id})")


# ─── HTTP API ──────────────────────────────────────────────────────────────────

async def handle_heartbeat(request: web.Request) -> web.Response:
    global clients
    try:
        data = await request.json()
        uuid = data.get("uuid", "").strip()
        if not uuid:
            return web.json_response({"ok": False, "error": "missing uuid"}, status=400)
        clients[uuid] = time.time()
        current = get_online()
        await update_peaks(current)
        return web.json_response({"ok": True, "online": current})
    except Exception as e:
        log.warning(f"heartbeat error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_online(request: web.Request) -> web.Response:
    return web.json_response({"online": get_online()})


async def handle_root(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "online": get_online()})


async def cleanup_loop():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        dead = [u for u, t in clients.items() if now - t >= TIMEOUT_SEC * 3]
        for u in dead:
            del clients[u]


# ─── MAIN ──────────────────────────────────────────────────────────────────────

async def main():
    await init_db()

    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_post("/heartbeat", handle_heartbeat)
    app.router.add_get("/online", handle_online)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", API_PORT)
    await site.start()
    log.info(f"HTTP API listening on :{API_PORT}")

    asyncio.create_task(cleanup_loop())

    log.info("Telegram bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
