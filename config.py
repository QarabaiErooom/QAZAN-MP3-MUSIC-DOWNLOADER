# config.py
"""QAZAN MP3 MUSIC DOWNLOADER — global configuration and logging."""

from __future__ import annotations

import logging
import os
import sys
from collections import deque
from datetime import datetime
from typing import Any, Deque, Dict

from colorama import Fore, Style
from colorama import init as colorama_init

# ----------------------------------------------------------------------------
# Bot / paths / defaults
# ----------------------------------------------------------------------------
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ART_PATH = rf"{BASE_DIR}\art.png"
COOKIES_PATH = "cookies.txt"  # relative to project root; optional
OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "Music")
DB_PATH = "qazan.db"
LOG_PATH = "system.log"

WEB_HOST = "127.0.0.1"
WEB_PORT = 8000

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

DEFAULT_SETTINGS: Dict[str, Any] = {
    "bitrate": "128k",
    "format": "mp3",
    "worker_count": 4,
    "max_concurrent": 4,
    "artist": "Qarabai Ermahan",
    "art_path": ART_PATH,
    "embed_cover": True,
    "output_dir": OUTPUT_DIR,
    "filename_format": "{title}-{artist}.mp3",
    "skip_existing": True,
    "max_retries": 3,
    "retry_delay_sec": 5,
    "progress_messages": True,
    "send_full_stats": True,
    "per_file_notification": False,
    "log_level": "INFO",
    "db_path": DB_PATH,
    "auto_cleanup_days": 30,
}

# Human-readable ring buffer consumed by the web dashboard.
RECENT_LOGS: Deque[Dict[str, str]] = deque(maxlen=1000)

_LEVEL_COLORS = {
    "info": Fore.CYAN,
    "ok": Fore.GREEN,
    "warn": Fore.YELLOW,
    "err": Fore.RED,
    "task": Fore.MAGENTA,
}

_colorama_ready = False


def resolve_path(path: str) -> str:
    """Resolve a possibly relative path against the project root."""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


def setup_logging() -> logging.Logger:
    """Configure the file logger (INFO+) and the console environment."""
    global _colorama_ready
    if not _colorama_ready:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        colorama_init()
        _colorama_ready = True

    logger = logging.getLogger("qazan")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        handler = logging.FileHandler(resolve_path(LOG_PATH), encoding="utf-8")
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiogram.dispatcher").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return logger


def human(message: str, level: str = "info") -> None:
    """Print a human-readable Kazakh line, store it, and mirror it to system.log."""
    ts = datetime.now().strftime("%H:%M:%S")
    RECENT_LOGS.append({"ts": ts, "level": level, "message": message})

    color = _LEVEL_COLORS.get(level, Fore.WHITE)
    try:
        print(f"{color}{message}{Style.RESET_ALL}", flush=True)
    except Exception:
        pass

    logging.getLogger("qazan").info("[%s] %s", level.upper(), message)