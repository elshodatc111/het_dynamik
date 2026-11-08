"""Fon ishlari va jonli progress (yengil, bazasiz).

Holat MEDIA_ROOT/jobs/<id>.json faylida turadi — shuning uchun istalgan gunicorn jarayoni
holatni o‘qiy oladi, bazaga esa hech narsa yozilmaydi. Og‘ir ishlar bir vaqtda bittadan
(fayl-qulf) bajariladi, shunda server bir nechta katta fayl bilan birdan yuklanmaydi.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

log = logging.getLogger(__name__)

_ID = re.compile(r"^[0-9a-f]{32}$")
STALE_SECONDS = 60          # shuncha vaqt yurak urishi bo‘lmasa — jarayon o‘lgan deb hisoblanadi
WRITE_EVERY = 0.35          # progress faylga ko‘pi bilan shuncha sekundda bir yoziladi
_last_clean = 0.0


class JobError(Exception):
    """Foydalanuvchiga ko‘rsatiladigan xato."""


def jobs_dir() -> Path:
    d = Path(settings.MEDIA_ROOT) / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(job_id: str) -> Path | None:
    return jobs_dir() / f"{job_id}.json" if _ID.match(job_id or "") else None


def _clean_old():
    global _last_clean
    now = time.time()
    if now - _last_clean < 600:
        return
    _last_clean = now
    for p in jobs_dir().glob("*"):
        try:
            if now - p.stat().st_mtime > 86400 and p.name != "heavy.lock":
                p.unlink(missing_ok=True)
        except OSError:
            pass


def ajax(request) -> bool:
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


class Job:
    def __init__(self, data: dict):
        self.d = data
        self._t = 0.0

    @property
    def id(self) -> str:
        return self.d["id"]

    # -- yozish
    def _flush(self, force=False):
        now = time.time()
        if not force and now - self._t < WRITE_EVERY:
            return
        self._t = now
        total = sum(w for _, w in self.d["steps"]) or 1
        i = min(self.d["step"], len(self.d["steps"]))
        done_w = sum(w for _, w in self.d["steps"][:i])
        cur_w = self.d["steps"][i][1] if i < len(self.d["steps"]) else 0
        pct = (done_w + cur_w * self.d["frac"]) / total * 100
        self.d["pct"] = 100.0 if self.d["status"] == "done" else round(min(pct, 99.5), 1)
        self.d["updated"] = now
        p = _path(self.id)
        tmp = p.with_name(f"{p.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, default=str), encoding="utf-8")
        for attempt in range(5):  # Windows: fayl o‘qilayotgan bo‘lsa almashtirish vaqtincha rad etilishi mumkin
            try:
                os.replace(tmp, p)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        tmp.unlink(missing_ok=True)

    def step(self, index: int, label: str | None = None):
        self.d.update(step=index, frac=0.0, status="running")
        if label is not None:
            self.d["label"] = label
        self._flush(force=True)

    def progress(self, frac: float | None = None, label: str | None = None):
        if frac is not None:
            self.d["frac"] = max(0.0, min(1.0, frac))
        if label is not None:
            self.d["label"] = label
        self._flush()

    def label(self, text: str):
        self.d["label"] = text
        self._flush(force=True)

    def set_result(self, result: dict):
        self.d["result"] = result

    def done(self, redirect_to: str | None = None):
        if redirect_to is not None:
            self.d["redirect"] = redirect_to
        self.d.update(status="done", step=len(self.d["steps"]), frac=0.0, label="Tayyor")
        self._flush(force=True)

    def fail(self, message: str):
        self.d.update(status="error", error=message)
        self._flush(force=True)


def create(kind: str, user, steps: list[tuple[str, int]], redirect_to: str = "", key: str = "", extra: dict | None = None) -> Job:
    _clean_old()
    d = {"id": uuid.uuid4().hex, "kind": kind, "user": getattr(user, "pk", None), "status": "queued",
         "steps": [[s, w] for s, w in steps], "step": 0, "frac": 0.0, "pct": 0.0, "label": "Navbatda…",
         "error": "", "redirect": redirect_to, "result": {}, "key": key, "created": time.time(), "updated": time.time(), **(extra or {})}
    job = Job(d)
    job._flush(force=True)
    if key:
        set_active(key, job.id)
    return job


def load(job_id: str, user=None) -> dict | None:
    """Holatni o‘qiydi. user berilsa, faqat shu foydalanuvchining ishi qaytadi."""
    p = _path(job_id)
    if not p:
        return None
    data = None
    for _ in range(3):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            break
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.05)
    if data is None:
        return {"id": job_id, "status": "running", "steps": [], "step": 0, "pct": 0, "label": "", "error": "", "redirect": "", "result": {}}
    if user is not None and data.get("user") != getattr(user, "pk", None):
        return None
    if data["status"] in ("queued", "running"):
        try:
            age = time.time() - p.stat().st_mtime
        except OSError:
            age = 0
        if age > STALE_SECONDS:
            data.update(status="error", error="Jarayon to‘xtab qoldi (server qayta ishga tushgan bo‘lishi mumkin). Qaytadan urinib ko‘ring.")
            try:
                p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
    return data


# -- faol ish (bir xil kalit uchun, masalan «send:12»)
def _active_file(key: str) -> Path:
    return jobs_dir() / f"active_{hashlib.sha1(key.encode()).hexdigest()[:16]}.txt"


def set_active(key: str, job_id: str):
    _active_file(key).write_text(job_id, encoding="utf-8")


def active(key: str) -> dict | None:
    f = _active_file(key)
    try:
        data = load(f.read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return data if data and data["status"] in ("queued", "running") else None


def public(data: dict) -> dict:
    return {k: data.get(k) for k in ("status", "pct", "step", "steps", "label", "error", "redirect")}


def status_response(request, job_id: str):
    data = load(job_id, request.user)
    if data is None:
        raise Http404
    resp = JsonResponse(public(data))
    resp["Cache-Control"] = "no-store"
    return resp


def status_url(job_id: str) -> str:
    return reverse("job_status", args=[job_id])


# -- bajarish
@contextmanager
def heavy_lock(job: Job, timeout=1800):
    """Og‘ir ishlar navbat bilan: bir vaqtda bittasi (server yuklanmasligi uchun)."""
    lock = jobs_dir() / "heavy.lock"
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, job.id.encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > STALE_SECONDS:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() > deadline:
                raise JobError("Navbat juda uzoq kutdi. Birozdan keyin qayta urinib ko‘ring.")
            job.label("Navbatda kutilmoqda — boshqa og‘ir jarayon tugashi kerak…")
            time.sleep(0.7)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def _heartbeat(job: Job, stop: threading.Event):
    p = _path(job.id)
    lock = jobs_dir() / "heavy.lock"
    while not stop.wait(8):
        now = time.time()
        for f in (p, lock):
            try:
                if f == p or (f.exists() and f.read_text() == job.id):
                    os.utime(f, (now, now))
            except OSError:
                pass


def _execute(job: Job, fn, args, kwargs, use_lock: bool, threaded: bool):
    stop = threading.Event()
    if threaded:
        threading.Thread(target=_heartbeat, args=(job, stop), daemon=True).start()
    try:
        with (heavy_lock(job) if use_lock else nullcontext()):
            job.d["status"] = "running"
            job.progress(0, "Boshlanmoqda…")
            fn(job, *args, **kwargs)
        if job.d["status"] != "done":
            job.done()
    except JobError as exc:
        job.fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Fon ishi xatosi (%s)", job.d.get("kind"))
        job.fail(f"Kutilmagan xato ({type(exc).__name__}). Qaytadan urinib ko‘ring.")
    finally:
        stop.set()
        if threaded:
            connection.close()


def launch(request, job: Job, fn, *args, use_lock=True, fail_url="/", **kwargs):
    """Ishni ishga tushiradi.

    AJAX so‘rov: orqa fonda (thread) boshlab, {job, status_url} qaytaradi.
    Oddiy so‘rov (JS yo‘q) yoki test: ishni shu zahoti bajarib, natija sahifasiga yo‘naltiradi."""
    sync = getattr(settings, "JOBS_SYNC", False)
    if ajax(request) and not sync:
        threading.Thread(target=_execute, args=(job, fn, args, kwargs, use_lock, True), daemon=True).start()
        return JsonResponse({"job": job.id, "status_url": status_url(job.id)})
    _execute(job, fn, args, kwargs, use_lock, False)
    if ajax(request):
        return JsonResponse({"job": job.id, "status_url": status_url(job.id)})
    data = load(job.id) or {}
    if data.get("status") == "done":
        return redirect(data.get("redirect") or fail_url)
    from django.contrib import messages
    messages.error(request, data.get("error") or "Jarayon bajarilmadi.")
    return redirect(fail_url)


def reject(request, message: str, fallback):
    """Tekshiruvdan o‘tmagan so‘rov: AJAX bo‘lsa JSON xato, aks holda xabar + sahifaga qaytish."""
    if ajax(request):
        return JsonResponse({"error": message}, status=400)
    from django.contrib import messages
    messages.error(request, message)
    return fallback()
