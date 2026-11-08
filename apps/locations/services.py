"""Location faylini fonda tekshirish va bazaga yozish (upsert), bot uchun qidiruv."""
from __future__ import annotations

import os
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from apps.core.jobs import JobError
from apps.reports.services import audit

from . import rules
from .models import LocationSearch, LocationUpload, Place

STEPS = [("Faylni o‘qish", 2), ("Qatorlarni tekshirish", 5), ("Bazaga yozish", 5), ("Natijani tayyorlash", 1)]
FIELDS = ["kind", "lat", "lon", "district", "substation", "feeder", "tp", "account", "fio", "address", "consumer_type"]
CHUNK = 400


def base_dir() -> Path:
    d = Path(settings.MEDIA_ROOT) / "location"
    (d / "tmp").mkdir(parents=True, exist_ok=True)
    (d / "errors").mkdir(parents=True, exist_ok=True)
    return d


def read_rows(path: Path, job=None) -> list[tuple[int, list]]:
    """1-qator — sarlavha. Qaytaradi: [(Excel qator raqami, {kalit: qiymat})]."""
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 — buzuq yoki Excel bo‘lmagan fayl
        raise JobError("Fayl o‘qilmadi. Faqat Excel (.xlsx) fayl yuklang.") from None
    try:
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        head = next(it, None)
        if head is None:
            raise JobError("Fayl bo‘sh.")
        mapping, err = rules.map_headers(head)
        if err:
            raise JobError(err)
        total = ws.max_row or 0
        rows = []
        for i, row in enumerate(it, 2):
            raw = {key: (row[idx] if idx < len(row) else None) for idx, key in mapping.items()}
            if all(rules.cell_text(v) == "" for v in raw.values()):
                continue
            rows.append((i, raw))
            if len(rows) > rules.MAX_ROWS:
                raise JobError(f"Faylda {rules.MAX_ROWS:,} tadan ko‘p qator bor.".replace(",", " "))
            if job and i % 1000 == 0:
                job.progress(i / max(total, i), f"O‘qilmoqda: {i:,} qator".replace(",", " "))
        if not rows:
            raise JobError("Faylda ma’lumot qatori topilmadi (2-qatordan boshlab to‘ldiring).")
        return rows
    finally:
        wb.close()


def run_upload(job, user_id: int, path: str, filename: str):
    """Fon ishi: o‘qish → tekshirish → upsert → xatolar fayli. job.done() ga jurnal sahifasiga yo‘nalish beriladi."""
    p = Path(path)
    user = get_user_model().objects.filter(pk=user_id).first()
    try:
        job.step(0, "Fayl ochilmoqda…")
        rows = read_rows(p, job)
        total = len(rows)

        job.step(1, f"Tekshirilmoqda: 0 / {total:,}".replace(",", " "))
        clean: list[tuple[int, dict]] = []
        bad: list[tuple[list, str]] = []
        seen: dict[str, int] = {}
        for n, (xl_row, raw) in enumerate(rows, 1):
            vals, errs = rules.validate_row(raw)
            if not errs and vals["number"] in seen:
                errs = [f"Nomer takrorlangan: faylda {seen[vals['number']]}-qatorda ham bor"]
            if errs:
                bad.append(([raw.get(k) for k in rules.KEYS], f"{xl_row}-qator: " + "; ".join(errs)))
            else:
                seen[vals["number"]] = xl_row
                clean.append((xl_row, vals))
            if n % 500 == 0:
                job.progress(n / total, f"Tekshirilmoqda: {n:,} / {total:,}".replace(",", " "))

        job.step(2, "Bazaga yozilmoqda…")
        created = updated = unchanged = 0
        with transaction.atomic():
            for start in range(0, len(clean), CHUNK):
                part = clean[start:start + CHUNK]
                existing = {pl.number: pl for pl in Place.objects.filter(number__in=[v["number"] for _r, v in part])}
                new, upd = [], []
                for _r, v in part:
                    pl = existing.get(v["number"])
                    if pl is None:
                        new.append(Place(**v))
                    elif any(getattr(pl, f) != v[f] for f in FIELDS):
                        for f in FIELDS:
                            setattr(pl, f, v[f])
                        upd.append(pl)
                    else:
                        unchanged += 1
                if new:
                    Place.objects.bulk_create(new, batch_size=CHUNK)
                if upd:
                    Place.objects.bulk_update(upd, FIELDS + ["updated_at"], batch_size=CHUNK)
                created += len(new)
                updated += len(upd)
                job.progress(min(1.0, (start + CHUNK) / max(len(clean), 1)),
                             f"Yozilmoqda: {min(start + CHUNK, len(clean)):,} / {len(clean):,}".replace(",", " "))

        job.step(3, "Natija tayyorlanmoqda…")
        err_name = ""
        if bad:
            err_name = f"errors/{job.id}.xlsx"
            (base_dir() / err_name).write_bytes(rules.build_error_file(bad))
        up = LocationUpload.objects.create(user=user, username=getattr(user, "username", "") or "", filename=filename[:200], total=total,
                                           created=created, updated=updated, unchanged=unchanged, bad=len(bad), error_file=err_name)
        audit(user, "location_upload", f"{filename}: jami {total}, yangi {created}, yangilandi {updated}, xato {len(bad)}")
        job.done(redirect_to=reverse("location_uploads") + f"?new={up.pk}")
    finally:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_tmp():
    now = time.time()
    for f in (base_dir() / "tmp").glob("*"):
        try:
            if now - f.stat().st_mtime > 86400:
                f.unlink(missing_ok=True)
        except OSError:
            pass


# ------------------------------------------------------------------ bot qidiruvi
def find_place(text: str):
    """Qaytaradi: (Place | None, qidiruv_matni, xato_matni). Xato — raqam ko‘rinishi noto‘g‘ri bo‘lsa."""
    q = rules.normalize_query(text)
    if not q or len(q) > 20 or not q.replace("-", "").isalnum():
        return None, (text or "").strip()[:60], "Raqam noto‘g‘ri ko‘rinishda. 12 xonali raqam yoki EX-1234567 yozing."
    pl = Place.objects.filter(number=q).first()
    if pl is None and q.isdigit() and len(q) in (6, 7):
        pl = Place.objects.filter(number=f"EX-{q}").first()
    return pl, q, ""


def log_search(bot_user, query: str, place) -> None:
    LocationSearch.objects.create(bot_user=bot_user, query=query[:60], found=place is not None, place=place)


def maps_buttons(pl) -> dict:
    lat, lon = f"{pl.lat:.7f}".rstrip("0"), f"{pl.lon:.7f}".rstrip("0")
    return {"inline_keyboard": [
        [{"text": "🗺 Google xarita", "url": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"},
         {"text": "🗺 Yandex xarita", "url": f"https://yandex.com/maps/?pt={lon},{lat}&z=17&l=map"}],
        [{"text": "🧭 Google yo‘nalish", "url": f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"},
         {"text": "🧭 Yandex yo‘nalish", "url": f"https://yandex.com/maps/?rtext=~{lat},{lon}&rtt=auto"}],
    ]}


KIND_TITLE = {"Meter": "Hisoblagich", "DCU": "Konsentrator (DCU)", "Balans": "Balans hisoblagich"}


def place_card(pl) -> str:
    lines = [f"✅ Topildi: {KIND_TITLE.get(pl.kind, pl.kind)}", "", f"🔢 Nomer: {pl.number}"]
    if pl.consumer_type and pl.kind == "Meter":
        lines.append(f"👥 Iste’molchi turi: {pl.consumer_type}")
    lines += [f"🏢 Tuman: {pl.district}", f"🏭 Podstansiya: {pl.substation}", f"⚡ Fider: {pl.feeder}", f"🔌 TP: {pl.tp}"]
    if pl.account:
        lines.append(f"📄 Hisob raqam: {pl.account}")
    if pl.fio:
        lines.append(f"👤 FIO: {pl.fio}")
    if pl.address:
        lines.append(f"🏠 Manzil: {pl.address}")
    lines += ["", f"📍 Koordinata: {pl.lat:.6f}, {pl.lon:.6f}", f"🕒 Yangilangan: {timezone.localtime(pl.updated_at):%d.%m.%Y}"]
    return "\n".join(lines)
