# web.py
"""FastAPI dashboard server with a live WebSocket feed."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import deque
from datetime import datetime
from typing import Any, Dict, List

import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.templating import Jinja2Templates

import config
import db
import downloader

log = logging.getLogger("qazan.web")

BASE = config.PROJECT_ROOT
TEMPLATES_DIR = os.path.join(BASE, "templates")
STATIC_DIR = os.path.join(BASE, "static")
LOG_FILE = config.resolve_path(config.LOG_PATH)

templates = Jinja2Templates(directory=TEMPLATES_DIR)

app = FastAPI(
    title="QAZAN MUSIC",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_LOG_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s*\|\s*"
    r"(?P<level>[A-Z]+)\s*\|\s*(?P<module>[^|]+?)\s*\|\s*(?P<message>.*)$"
)


class SettingUpdate(BaseModel):
    key: str
    value: Any


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    template = templates.env.get_template("index.html")
    return HTMLResponse(template.render())


@app.get("/api/settings")
async def api_settings() -> Dict[str, Any]:
    values = await db.get_all_settings()
    return {"values": values, "order": list(config.DEFAULT_SETTINGS.keys())}


@app.post("/api/settings")
async def api_set_setting(payload: SettingUpdate) -> JSONResponse:
    if payload.key not in config.DEFAULT_SETTINGS:
        return JSONResponse(
            {"ok": False, "error": "unknown setting key"}, status_code=400
        )
    await db.set_setting(payload.key, payload.value)
    downloader.pool.request_reload()
    config.human(f"⚙️  Параметр өзгертілді: {payload.key} = {payload.value}", "info")
    return JSONResponse(
        {
            "ok": True,
            "key": payload.key,
            "value": await db.get_setting(payload.key),
        }
    )


@app.get("/api/queue")
async def api_queue() -> Dict[str, Any]:
    return {"tasks": await db.get_queue()}


@app.get("/api/history")
async def api_history(limit: int = Query(100, ge=1, le=500)) -> Dict[str, Any]:
    return {"tasks": await db.get_history(limit)}


@app.get("/api/stats")
async def api_stats() -> Dict[str, Any]:
    return await db.get_dashboard_stats()


@app.get("/api/logs")
async def api_logs(lines: int = Query(200, ge=1, le=1000)) -> Dict[str, Any]:
    return {"entries": _read_log_tail(lines), "path": LOG_FILE}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await _build_payload()
            await websocket.send_json(payload)
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("WebSocket loop failed")
        try:
            await websocket.close()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
async def _build_payload() -> Dict[str, Any]:
    snapshot = downloader.pool.snapshot()
    stats = await db.get_dashboard_stats()
    queue_tasks = await db.get_queue()
    history = await db.get_history(40)
    recent = list(config.RECENT_LOGS)[-20:]
    terminal = list(config.RECENT_LOGS)[-120:]

    return {
        "time": datetime.now().strftime("%H:%M:%S"),
        "workers": snapshot["workers"],
        "queue": {"pending": snapshot["pending"], "active": snapshot["active"]},
        "stats": stats["today"],
        "avg_speed": stats["avg_speed"],
        "daily": stats["daily"],
        "recent_tasks": stats["recent"],
        "queue_tasks": queue_tasks,
        "history": history,
        "recent_logs": recent,
        "terminal": terminal,
    }


def _read_log_tail(limit: int) -> List[Dict[str, str]]:
    if not os.path.isfile(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as handle:
            tail = deque(handle, maxlen=max(1, int(limit)))
    except OSError:
        log.exception("Failed to read log file")
        return []

    entries: List[Dict[str, str]] = []
    for raw in tail:
        line = raw.rstrip("\n")
        match = _LOG_LINE_RE.match(line)
        if match:
            entries.append(
                {
                    "ts": match.group("ts"),
                    "level": match.group("level"),
                    "module": match.group("module").strip(),
                    "message": match.group("message"),
                }
            )
        elif line.strip():
            entries.append(
                {"ts": "", "level": "INFO", "module": "system", "message": line}
            )
    return entries


async def run_web() -> None:
    config.human(
        f"🌐 Веб-панель: http://{config.WEB_HOST}:{config.WEB_PORT}", "ok"
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.WEB_HOST,
            port=config.WEB_PORT,
            log_level="warning",
            access_log=False,
        )
    )
    await server.serve()