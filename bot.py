"""
RunTime Visuals — Online Tracker Bot
=====================================
Telegram бот + HTTP API сервер в одном процессе.

HTTP API (порт 8080):
  POST /heartbeat   body: {"uuid": "...", "username": "..."}  → {"ok": true}
  GET  /online                                                 → {"online": 42}

Telegram команды:
  /online  — показать текущий онлайн
  /stats   — онлайн + пик за сессию
"""

import asyncio
import time
import logging
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# ─── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ["BOT_TOKEN"]
API_PORT    = int(os.environ.get("PORT", 8080))
TIMEOUT_SEC = 90
# ───────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# uuid -> last_seen timestamp
clients: dict[str, float] = {}
peak_online: int = 0


def get_online() -> int:
    now = time.time()
    return sum(1 for t in clients.values() if now - t < TIMEOUT_SEC)


# ─── HTTP API ──────────────────────────────────────────────────────────────────

async def handle_heartbeat(request: web.Request) -> web.Response:
    global peak_online
    try:
        data = await request.json()
        uuid = data.get("uuid", "").strip()
        if not uuid:
            return web.json_response({"ok": False, "error": "missing uuid"}, status=400)
        clients[uuid] = time.time()
        current = get_online()
        if current > peak_online:
            peak_online = current
        return web.json_response({"ok": True, "online": current})
    except Exception as e:
        log.warning(f"heartbeat error: {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def handle_online(request: web.Request) -> web.Response:
    return web.json_response({"online": get_online()})


async def handle_root(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "online": get_online()})


async def cleanup_loop():
    """Каждые 60 сек чистим мёртвые записи чтобы dict не рос вечно."""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        dead = [u for u, t in clients.items() if now - t >= TIMEOUT_SEC * 3]
        for u in dead:
            del clients[u]
        if dead:
            log.info(f"Cleaned {len(dead)} stale clients, online={get_online()}")


# ─── TELEGRAM ──────────────────────────────────────────────────────────────────

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()


@dp.message(Command("online"))
async def cmd_online(message: types.Message):
    await message.answer(f"🟢 Онлайн сейчас: <b>{get_online()}</b> игроков", parse_mode="HTML")


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    await message.answer(
        f"📊 <b>Статистика RunTime Visuals</b>\n"
        f"🟢 Сейчас онлайн: <b>{get_online()}</b>\n"
        f"🏆 Пик за сессию: <b>{peak_online}</b>",
        parse_mode="HTML"
    )


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 <b>RunTime Visuals Bot</b>\n\n"
        "/online — текущий онлайн\n"
        "/stats  — онлайн + пик",
        parse_mode="HTML"
    )


# ─── MAIN ──────────────────────────────────────────────────────────────────────

async def main():
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
