# downloader.py
"""yt-dlp + ffmpeg download pipeline and the persistent worker pool."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional

import yt_dlp

import config
import db

log = logging.getLogger("qazan.downloader")

_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def _short(text: str, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def sanitize_name(name: str, limit: int = 110) -> str:
    cleaned = _BAD_FILENAME_CHARS.sub("", str(name or ""))
    cleaned = cleaned.replace("\n", " ").replace("\r", " ").strip().strip(".")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip()
    return cleaned or "audio"


def build_final_path(title: str, artist: str, settings: Dict[str, Any]) -> str:
    output_dir = str(settings.get("output_dir") or config.OUTPUT_DIR)
    pattern = str(settings.get("filename_format") or "{title}-{artist}.mp3")
    safe_title = sanitize_name(title)
    safe_artist = sanitize_name(artist or "Unknown", 60)
    try:
        filename = pattern.format(title=safe_title, artist=safe_artist)
    except (KeyError, IndexError, ValueError):
        filename = f"{safe_title}-{safe_artist}.mp3"
    filename = sanitize_name(filename, 150)
    if not filename.lower().endswith(".mp3"):
        filename += ".mp3"
    return os.path.join(output_dir, filename)


def _base_opts(settings: Dict[str, Any]) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "ignoreerrors": False,
        "retries": 2,
        "socket_timeout": 30,
        "noprogress": True,
        "cachedir": False,
        "logger": _YdlLogger(),
    }
    cookies = config.resolve_path(config.COOKIES_PATH)
    if os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    return opts


class _YdlLogger:
    """Route yt-dlp messages to system.log only (never to stdout)."""

    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp: %s", msg)


def _extract_sync(url: str, settings: Dict[str, Any]) -> Dict[str, Any]:
    opts = _base_opts(settings)
    opts["skip_download"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("Бейне ақпараты алынбады")
    if "entries" in info:
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            raise RuntimeError("Бейне табылмады")
        info = entries[0]
    return info


def _progress_hook(state: Dict[str, Any]):
    def hook(data: Dict[str, Any]) -> None:
        try:
            if data.get("status") == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                done = data.get("downloaded_bytes") or 0
                if total:
                    state["progress"] = round(min(100.0, done * 100.0 / total), 1)
            elif data.get("status") == "finished":
                state["progress"] = 100.0
        except Exception:
            pass

    return hook


def _download_sync(url: str, tmpdir: str, settings: Dict[str, Any], state: Dict[str, Any]) -> str:
    bitrate = str(settings.get("bitrate") or "128k").lower().replace("kbps", "").replace("k", "")
    bitrate = re.sub(r"[^0-9]", "", bitrate) or "128"

    opts = _base_opts(settings)
    opts.update(
        {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": str(settings.get("format") or "mp3"),
                    "preferredquality": bitrate,
                }
            ],
            "progress_hooks": [_progress_hook(state)],
        }
    )

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    candidates = [
        os.path.join(tmpdir, name)
        for name in os.listdir(tmpdir)
        if name.lower().endswith(".mp3")
    ]
    if not candidates:
        raise RuntimeError("MP3 файл жасалмады (ffmpeg жоқ болуы мүмкін)")
    return max(candidates, key=os.path.getsize)


def _embed_sync(
    src: str,
    dst: str,
    title: str,
    artist: str,
    art_path: str,
    embed_cover: bool,
) -> None:
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)

    use_cover = bool(embed_cover) and bool(art_path) and os.path.isfile(art_path)
    if embed_cover and not use_cover:
        log.warning("Cover art missing: %s", art_path)
        config.human("⚠️  Cover art табылмады — суретсіз жалғастырамыз", "warn")

    cmd: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        src,
    ]
    if use_cover:
        cmd += [
            "-i",
            art_path,
            "-map",
            "0:a",
            "-map",
            "1:v",
            "-c",
            "copy",
            "-id3v2_version",
            "3",
            "-metadata:s:v",
            "title=Album cover",
            "-metadata:s:v",
            "comment=Cover (Front)",
        ]
    else:
        cmd += ["-map", "0:a", "-c:a", "copy", "-id3v2_version", "3"]

    cmd += [
        "-metadata",
        f"artist={artist}",
        "-metadata",
        f"title={title}",
        dst,
    ]

    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        **kwargs,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {(proc.stderr or '').strip()[:300] or 'unknown error'}")


# ----------------------------------------------------------------------------
# Notifier bridge (telegram_bot registers itself)
# ----------------------------------------------------------------------------
class Notifier:
    async def task_failed(self, chat_id: int, url: str, error: str) -> None:  # pragma: no cover
        pass

    async def task_finished(self, chat_id: int, task_id: int) -> None:  # pragma: no cover
        pass


_notifier: Optional[Notifier] = None


def set_notifier(notifier: Notifier) -> None:
    global _notifier
    _notifier = notifier


async def _notify_failure(chat_id: Optional[int], url: str, error: str) -> None:
    if not _notifier or not chat_id:
        return
    try:
        await _notifier.task_failed(int(chat_id), url, error)
    except Exception:
        log.exception("Notifier.task_failed failed")


async def _notify_finished(chat_id: Optional[int], task_id: int) -> None:
    if not _notifier or not chat_id:
        return
    try:
        await _notifier.task_finished(int(chat_id), task_id)
    except Exception:
        log.exception("Notifier.task_finished failed")


# ----------------------------------------------------------------------------
# Worker pool
# ----------------------------------------------------------------------------
class WorkerPool:
    def __init__(self) -> None:
        self.queue: Optional[asyncio.Queue] = None
        self.workers: Dict[int, asyncio.Task] = {}
        self.stop_flags: Dict[int, asyncio.Event] = {}
        self.status: Dict[int, Dict[str, Any]] = {}
        self._settings_event: Optional[asyncio.Event] = None
        self._sem: Optional[asyncio.Semaphore] = None
        self._sem_limit = 4
        self._shutdown = False
        self._task_counter = 0

    # -- lifecycle ---------------------------------------------------------
    def _ensure(self) -> None:
        if self.queue is None:
            self.queue = asyncio.Queue()
        if self._settings_event is None:
            self._settings_event = asyncio.Event()
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._sem_limit)

    def prepare(self) -> None:
        self._ensure()

    def request_reload(self) -> None:
        self._ensure()
        assert self._settings_event is not None
        self._settings_event.set()

    async def enqueue(self, task_id: int, url: str) -> None:
        self._ensure()
        assert self.queue is not None
        self._task_counter += 1
        await self.queue.put((task_id, url))

    # -- introspection -----------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        self._ensure()
        workers: List[Dict[str, Any]] = []
        for wid in sorted(self.status.keys()):
            state = self.status[wid]
            workers.append(
                {
                    "id": wid,
                    "status": state.get("status", "idle"),
                    "current_url": state.get("current_url"),
                    "progress": float(state.get("progress", 0.0)),
                    "title": state.get("title"),
                }
            )
        active = sum(
            1 for s in self.status.values() if s.get("status") in ("downloading", "processing")
        )
        pending = self.queue.qsize() if self.queue is not None else 0
        return {"workers": workers, "pending": pending, "active": active}

    # -- supervisor --------------------------------------------------------
    async def run(self) -> None:
        self._ensure()
        config.human("⚙️  Worker пулы іске қосылуда", "info")
        await self._apply_settings()
        while not self._shutdown:
            assert self._settings_event is not None
            try:
                await asyncio.wait_for(self._settings_event.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            self._settings_event.clear()
            try:
                await self._apply_settings()
            except Exception:
                log.exception("Failed to apply worker settings")

    async def _apply_settings(self) -> None:
        settings = await db.get_all_settings()
        try:
            count = max(1, int(settings.get("worker_count", 4)))
        except (TypeError, ValueError):
            count = 4
        try:
            limit = max(1, int(settings.get("max_concurrent", 4)))
        except (TypeError, ValueError):
            limit = 4

        if limit != self._sem_limit or self._sem is None:
            self._sem_limit = limit
            self._sem = asyncio.Semaphore(limit)

        for wid in list(self.workers.keys()):
            if wid > count:
                flag = self.stop_flags.get(wid)
                if flag is not None:
                    flag.set()

        for wid in range(1, count + 1):
            task = self.workers.get(wid)
            if task is None or task.done():
                if wid in self.stop_flags:
                    continue
                self.stop_flags[wid] = asyncio.Event()
                self.workers[wid] = asyncio.create_task(
                    self._worker(wid), name=f"qazan-worker-{wid}"
                )

    # -- worker ------------------------------------------------------------
    async def _worker(self, wid: int) -> None:
        self._ensure()
        stop_flag = self.stop_flags[wid]
        self.status[wid] = {
            "id": wid,
            "status": "idle",
            "current_url": None,
            "progress": 0.0,
            "title": None,
        }
        log.info("Worker %s started", wid)
        config.human(f"🧵 Worker {wid} іске қосылды", "info")

        try:
            while not stop_flag.is_set():
                assert self.queue is not None
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=0.75)
                except asyncio.TimeoutError:
                    continue

                task_id, url = item
                try:
                    assert self._sem is not None
                    sem = self._sem
                    async with sem:
                        await self._run_task(wid, task_id, url)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Worker %s crashed on task %s", wid, task_id)
                    try:
                        await db.update_task_status(
                            task_id,
                            status="failed",
                            error_msg="internal worker error",
                            finished_at=_now_iso(),
                        )
                    except Exception:
                        log.exception("Failed to mark crashed task %s", task_id)
                finally:
                    try:
                        self.queue.task_done()
                    except ValueError:
                        pass
                    if wid in self.status:
                        self.status[wid].update(
                            status="idle", current_url=None, progress=0.0, title=None
                        )
        finally:
            self.status.pop(wid, None)
            self.workers.pop(wid, None)
            self.stop_flags.pop(wid, None)
            log.info("Worker %s stopped", wid)
            config.human(f"🧵 Worker {wid} тоқтады", "info")

    async def _run_task(self, wid: int, task_id: int, url: str) -> None:
        settings = await db.get_all_settings()
        task = await db.get_task(task_id)
        chat_id = task.get("chat_id") if task else None

        try:
            max_retries = max(0, int(settings.get("max_retries", 3)))
        except (TypeError, ValueError):
            max_retries = 3
        try:
            delay = max(0.0, float(settings.get("retry_delay_sec", 5)))
        except (TypeError, ValueError):
            delay = 5.0

        attempt = 0
        while True:
            attempt += 1
            started = time.time()
            await db.update_task_status(
                task_id,
                status="downloading",
                worker_id=wid,
                attempts=attempt,
                started_at=_now_iso(),
                error_msg=None,
            )
            self.status[wid].update(
                status="downloading", current_url=url, progress=0.0, title=None
            )
            try:
                result = await self._attempt(wid, task_id, url, settings)
                if result["status"] == "skipped":
                    await db.update_task_status(
                        task_id,
                        status="skipped",
                        file_path=result.get("path"),
                        finished_at=_now_iso(),
                    )
                    await db.record_task_result("skipped", 0)
                    config.human(
                        f"⏭️  Өткізілді: {result.get('title') or url}", "warn"
                    )
                else:
                    size = int(result.get("size") or 0)
                    await db.update_task_status(
                        task_id,
                        status="done",
                        file_path=result.get("path"),
                        file_size=size,
                        duration_sec=round(time.time() - started, 2),
                        finished_at=_now_iso(),
                    )
                    await db.record_task_result("done", size)
                    config.human(
                        f"✅ Дайын: {os.path.basename(str(result.get('path') or ''))}", "ok"
                    )
                await _notify_finished(chat_id, task_id)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                log.error(
                    "Task %s attempt %s/%s failed: %s",
                    task_id,
                    attempt,
                    max_retries + 1,
                    error_text,
                    exc_info=True,
                )
                if attempt <= max_retries:
                    config.human(f"⚠️  Қайталау {attempt}/{max_retries}: {url}", "warn")
                    await asyncio.sleep(delay)
                    continue
                await db.update_task_status(
                    task_id,
                    status="failed",
                    error_msg=error_text[:800],
                    finished_at=_now_iso(),
                )
                await db.record_task_result("failed", 0)
                config.human(
                    f"❌ Сәтсіз: {url} — себебі: {_short(error_text)}", "err"
                )
                await _notify_failure(chat_id, url, error_text)
                await _notify_finished(chat_id, task_id)
                return
            finally:
                if wid in self.status:
                    self.status[wid].update(
                        status="idle", current_url=None, progress=0.0
                    )

    async def _attempt(
        self,
        wid: int,
        task_id: int,
        url: str,
        settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        info = await asyncio.to_thread(_extract_sync, url, settings)
        title = str(info.get("title") or "audio").strip()
        await db.update_task_status(task_id, video_title=title)
        self.status[wid]["title"] = title

        artist = str(settings.get("artist") or "Unknown")
        final_path = build_final_path(title, artist, settings)

        if bool(settings.get("skip_existing", True)) and os.path.isfile(final_path):
            return {"status": "skipped", "path": final_path, "title": title}

        if await db.has_done_title(title):
            return {"status": "skipped", "path": final_path, "title": title}

        config.human(f"⬇️  Жүктелуде: {title} (Worker {wid})", "task")

        tmpdir = tempfile.mkdtemp(prefix="qazan_")
        try:
            mp3_path = await asyncio.to_thread(
                _download_sync, url, tmpdir, settings, self.status[wid]
            )
            await db.update_task_status(task_id, status="processing")
            self.status[wid]["status"] = "processing"

            art_path = str(settings.get("art_path") or config.ART_PATH)
            await asyncio.to_thread(
                _embed_sync,
                mp3_path,
                final_path,
                title,
                artist,
                art_path,
                bool(settings.get("embed_cover", True)),
            )

            if not os.path.isfile(final_path) or os.path.getsize(final_path) == 0:
                raise RuntimeError("ffmpeg шығыс файлы жасалмады")

            return {
                "status": "done",
                "path": final_path,
                "size": os.path.getsize(final_path),
                "title": title,
            }
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


pool = WorkerPool()


async def restore_pending() -> None:
    """Re-enqueue tasks that were pending or interrupted by a restart."""
    pool.prepare()
    rows = await db.get_queue()
    restored = 0
    for row in rows:
        status = row.get("status")
        if status in ("downloading", "processing"):
            await db.update_task_status(int(row["id"]), status="pending", worker_id=None)
        await pool.enqueue(int(row["id"]), str(row["url"]))
        restored += 1
    if restored:
        config.human(f"♻️  Кезек қалпына келтірілді: {restored}", "info")