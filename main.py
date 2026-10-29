# main.py
"""QAZAN MP3 MUSIC DOWNLOADER — single entry point."""

from __future__ import annotations

import asyncio
import logging
import os

import config
import db
import downloader
import telegram_bot
import web

log = logging.getLogger("qazan.main")


async def main() -> None:
    config.setup_logging()
    config.human("🚀 QAZAN MUSIC іске қосылуда…", "ok")

    await db.init_db()
    settings = await db.get_all_settings()

    output_dir = str(settings.get("output_dir") or config.OUTPUT_DIR)
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError:
        log.exception("Cannot create output directory: %s", output_dir)
        config.human("⚠️  Шығыс қалтасын жасау мүмкін болмады", "warn")

    try:
        removed = await db.cleanup_old_tasks(int(settings.get("auto_cleanup_days", 30)))
        if removed:
            config.human(f"🧹 Ескі жазбалар тазаланды: {removed}", "info")
    except Exception:
        log.exception("Cleanup failed")

    downloader.pool.prepare()
    await downloader.restore_pending()

    config.human("✅ Жүйе дайын, барлық қызметтер қосылуда", "ok")

    await asyncio.gather(
        telegram_bot.run_bot(),
        web.run_web(),
        downloader.pool.run(),
    )


def _run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        config.human("⏹  Жүйе тоқтатылды", "warn")
    except Exception:
        logging.getLogger("qazan.main").exception("Fatal error")
        config.human("❌ Жүйе апаттық қатемен тоқтады (system.log қараңыз)", "err")


if __name__ == "__main__":
    _run()