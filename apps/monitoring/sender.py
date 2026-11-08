"""Telegram guruhga hisobotni yuborish: svod rasmi + tanlangan Excel fayllar. Orqa fonda, jonli progress bilan."""
from __future__ import annotations

import logging
from django.urls import reverse
from django.utils import timezone

from apps.core import jobs

from . import exports, image, services, telegram
from .models import SendLog, Snapshot

log = logging.getLogger(__name__)


def build_text(snap: Snapshot) -> str:
    s = snap.summary or {}
    dl = services.deltas(snap)
    f, fd = services.fmt_int, services.fmt_delta
    lines = [f"📊 Offline monitoring — {snap.report_date:%d.%m.%Y}"]
    dcu = s.get("dcu")
    if dcu:
        labels = services.bucket_labels(s["dcu_edges"])
        tot = f"🔌 Konsentratorlar ({s['dcu_edges'][0]}+ kun offline): {f(dcu['over_min'])} ta"
        if dl["dcu_total"] is not None:
            tot += f" ({fd(dl['dcu_total'])})"
        lines += ["", tot]
        lines += [f"  • {lb}: {f(c)}" for lb, c in zip(labels, dcu["buckets"])]
        if dcu.get("new") is not None:
            lines.append(f"  Yangi offline: {f(dcu['new'])} · tiklangan: {f(dcu['recovered'])}")
    mt = s.get("meters")
    if mt:
        labels = services.bucket_labels(s["meter_edges"])
        tot = f"📟 Aloqasiz hisoblagichlar ({s['meter_edges'][0]}+ kun): {f(mt['over_min'])} ta"
        if dl["meter_total"] is not None:
            tot += f" ({fd(dl['meter_total'])})"
        lines += ["", tot]
        for i, lb in enumerate(labels):
            m = mt["matrix"][i]
            lines.append(f"  • {lb}: {f(mt['bucket_totals'][i])} (yuridik {f(m['yuridik'])}, aholi {f(m['aholi'])}, balans {f(m['balans'])}"
                         + (f", boshqa {f(m['boshqa'])}" if m["boshqa"] else "") + ")")
        g = mt["groups_total"]
        lines.append(f"  Jami: yuridik {f(g['yuridik'])} · aholi {f(g['aholi'])} · balans {f(g['balans'])}"
                     + (f" · boshqa {f(g['boshqa'])}" if g["boshqa"] else ""))
    lines += ["", "Nomma-nom ro‘yxatlar Excel fayllarda 👇"]
    return "\n".join(lines)


def _short_label(label: str) -> str:
    return label.replace(" — nomma-nom", "")


def steps_for(snap: Snapshot, kinds: list[str]) -> list[tuple[str, int]]:
    labels = {x["kind"]: _short_label(x["label"]) for x in exports.available_files(snap)}
    return [("Svod rasmi", 3)] + [(labels.get(k, k), 4) for k in kinds]


def job_key(snap: Snapshot) -> str:
    return f"send:{snap.pk}"


def running_job(snap: Snapshot):
    """Shu hisobot uchun hozir ishlayotgan yuborish ishi (yo‘q bo‘lsa None). Qotib qolgan jurnal yozuvlarini tuzatadi."""
    job = jobs.active(job_key(snap))
    if job is None:
        stuck = SendLog.objects.filter(snapshot=snap, status="running")
        if stuck.exists():
            stuck.update(status="error", detail="Yuborish to‘xtab qoldi (server qayta ishga tushgan bo‘lishi mumkin). Qaytadan yuboring.",
                         finished_at=timezone.now())
    return job


def create_log(snap: Snapshot, user, kinds: list[str], chat_id: str) -> SendLog:
    return SendLog.objects.create(snapshot=snap, user=user if getattr(user, "pk", None) else None,
                                  username=getattr(user, "username", "") or "", chat_id=chat_id, kinds=kinds)


def send_job(job, pk: int):
    """Orqa fonda: svod rasmi va tanlangan fayllarni ketma-ket yuboradi; har qadam jonli ko‘rinadi."""
    row = SendLog.objects.select_related("snapshot").get(pk=pk)
    snap = row.snapshot
    sent = []
    try:
        job.step(0, "Svod rasmi tayyorlanmoqda…")
        with jobs.heavy_lock(job):
            png = image.svod_png(snap)
        job.progress(0.5, "Svod rasmi Telegramga yuborilmoqda…")
        telegram.send_photo(row.chat_id, f"svod_{snap.report_date:%Y-%m-%d}.png", png,
                            caption=f"📊 Offline monitoring svodi · {snap.report_date:%d.%m.%Y}")
        for i, kind in enumerate(row.kinds):
            job.step(1 + i, "Excel fayl shakllantirilmoqda…")
            with jobs.heavy_lock(job):
                data, name = exports.build_file(snap, kind)
            label = next((x["label"] for x in exports.available_files(snap) if x["kind"] == kind), name)
            job.progress(0.5, f"{name} · {len(data) / 1048576:.1f} MB Telegramga yuborilmoqda…")
            telegram.send_document(row.chat_id, name, data, caption=f"{label} · {snap.report_date:%d.%m.%Y}")
            sent.append(name)
        row.status, row.detail = "ok", f"Svod rasmi va {len(sent)} ta fayl yuborildi: " + ", ".join(sent)
        err = None
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram yuborish xatosi: %s", type(exc).__name__)
        msg = str(exc) if isinstance(exc, telegram.TelegramError) else f"Kutilmagan xato ({type(exc).__name__})"
        row.status = "error"
        row.detail = f"{msg}" + (f" · yuborilganlari: {', '.join(sent)}" if sent else "")
        err = msg
    row.finished_at = timezone.now()
    row.save(update_fields=["status", "detail", "finished_at"])
    from apps.reports.services import audit
    audit(row.user, "mon_send_" + row.status, f"{snap.report_date:%d.%m.%Y} · {row.detail}"[:3900])
    if err:
        raise jobs.JobError(err)
    job.done(reverse("mon_send", args=[snap.pk]))
