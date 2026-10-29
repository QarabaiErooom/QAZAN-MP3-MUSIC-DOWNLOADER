# telegram_bot.py
"""aiogram 3 Telegram bot for QAZAN."""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Set

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

import config
import db
import downloader

log = logging.getLogger("qazan.bot")

START_TEXT = (
    "Сәлем! Мен Qazan. Маған YouTube немесе YouTube Shorts сілтемелерін жібер — "
    "қалағаныңша, бір хабарламада бірнеше сілтеме болса да қабылдаймын. "
    "Барлығын кезекке қойып, 128 kbps MP3 етіп жүктеп беремін."
)

NO_URL_TEXT = "Сілтеме табылмады. YouTube сілтемесін жіберіңіз."

URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)/[^\s<>\"']+",
    re.IGNORECASE,
)

_TRAILING = ").,;:!?'\"»…"

# chat_id -> {"start": float, "ids": List[int], "remaining": Set[int]}
_batches: Dict[int, Dict[str, object]] = {}


def _short(text: str, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _fmt_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _fmt_bytes(value: float) -> str:
    size = float(value or 0)
    if size < 1024:
        return f"{int(size)} B"
    units = ["KB", "MB", "GB", "TB"]
    index = -1
    while size >= 1024 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    return f"{size:.1f} {units[index]}"


def _fmt_speed(bytes_per_sec: float) -> str:
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "—"
    return f"{_fmt_bytes(bytes_per_sec)}/s"


def extract_urls(text: Optional[str]) -> List[str]:
    if not text:
        return []
    found: List[str] = []
    seen: Set[str] = set()
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(_TRAILING)
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(url)
    return found


class TelegramNotifier(downloader.Notifier):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def _send(self, chat_id: int, text: str) -> None:
        try:
            await self.bot.send_message(chat_id, text)
        except Exception:
            log.exception("Failed to send message to chat %s", chat_id)

    async def task_failed(self, chat_id: int, url: str, error: str) -> None:
        await self._send(
            chat_id,
            f"⚠️ Бұл сілтеме жүктелмеді: {url}\nСебебі: {_short(error)}",
        )

    async def task_finished(self, chat_id: int, task_id: int) -> None:
        if not chat_id:
            return
        batch = _batches.get(chat_id)
        if not batch:
            return
        remaining = batch["remaining"]
        assert isinstance(remaining, set)
        remaining.discard(task_id)
        if remaining:
            return
        _batches.pop(chat_id, None)
        await self._send_final(chat_id, batch)

    async def _send_final(self, chat_id: int, batch: Dict[str, object]) -> None:
        ids = [int(i) for i in batch["ids"]]  # type: ignore[arg-type]
        rows = await db.get_tasks_by_ids(ids)
        total = len(rows)
        success = sum(1 for row in rows if row.get("status") == "done")
        failed = sum(1 for row in rows if row.get("status") == "failed")
        skipped = sum(1 for row in rows if row.get("status") == "skipped")
        duration = time.time() - float(batch["start"])  # type: ignore[arg-type]

        total_bytes = sum(int(row.get("file_size") or 0) for row in rows)
        total_seconds = sum(float(row.get("duration_sec") or 0.0) for row in rows)
        speed = total_bytes / total_seconds if total_seconds > 0 and total_bytes > 0 else 0.0

        text = (
            "✅ Жүктеу аяқталды!\n"
            f"Жалпы: {total} | Сәтті: {success} | Сәтсіз: {failed} | Өткізілді: {skipped}\n"
            f"Уақыт: {_fmt_duration(duration)}\n"
            f"Орташа жылдамдық: {_fmt_speed(speed)}"
        )
        await self._send(chat_id, text)


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()

    @dispatcher.message(CommandStart())
    async def on_start(message: Message) -> None:
        await message.answer(START_TEXT)

    @dispatcher.message(F.text)
    async def on_text(message: Message) -> None:
        urls = extract_urls(message.text)
        if not urls:
            await message.answer(NO_URL_TEXT)
            return
        await _handle_urls(message, urls)

    return dispatcher


async def _handle_urls(message: Message, urls: List[str]) -> None:
    settings = await db.get_all_settings()
    chat_id = int(message.chat.id)

    batch = _batches.get(chat_id)
    if batch is None:
        batch = {"start": time.time(), "ids": [], "remaining": set()}
        _batches[chat_id] = batch

    ids_list = batch["ids"]
    remaining = batch["remaining"]
    assert isinstance(ids_list, list) and isinstance(remaining, set)

    for url in urls:
        task_id = await db.enqueue_task(url, chat_id=chat_id)
        ids_list.append(task_id)
        remaining.add(task_id)
        await downloader.pool.enqueue(task_id, url)

    config.human(f"🎵 Кезекке {len(urls)} сілтеме қосылды", "ok")

    if bool(settings.get("progress_messages", True)):
        snap = downloader.pool.snapshot()
        try:
            worker_count = int(settings.get("worker_count", 4))
        except (TypeError, ValueError):
            worker_count = 4
        text = (
            f"🎵 Кезекке {len(urls)} өлең тіркелді. "
            f"Қазір {snap['active']}/{worker_count} жүктелуде. "
            "Барлығы біткенде толық статистика жіберемін."
        )
        try:
            await message.answer(text)
        except Exception:
            log.exception("Failed to send queue acknowledgement to %s", chat_id)


async def run_bot() -> None:
    token = config.BOT_TOKEN
    if not token or "YOUR_BOT_TOKEN" in token:
        config.human("⚠️  BOT_TOKEN орнатылмаған — Telegram бот өшірулі", "warn")
        return

    bot = Bot(token=token)
    dispatcher = build_dispatcher()
    downloader.set_notifier(TelegramNotifier(bot))

    config.human("🤖 Telegram бот іске қосылды", "ok")
    try:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            log.exception("delete_webhook failed")
        await dispatcher.start_polling(bot)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Telegram polling crashed")
        config.human("❌ Telegram бот тоқтады", "err")
    finally:
        try:
            await bot.session.close()
        except Exception:
            log.exception("Failed to close bot session")


import asyncio  # noqa: E402  (kept at the bottom to avoid shadowing in handlers)