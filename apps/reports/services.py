"""Biznes-mantiq: katalog, rollar, saqlash, solishtirish, dashboard va trend."""
from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.files import File
from django.db import transaction

from .models import AuditLog, District, Metric, MetricValue, Section, Upload
from .parser.catalog import SECTIONS
from .parser.districts import DISTRICTS, DISTRICT_KEYS, TOTAL_KEY, TOTAL_NAME
from .parser.extract import ParsedReport, parse_pdf
from .parser.validate import check_against_db, validate

# ------------------------------------------------------------------ rollar
from .perms import PERM_GROUPS, PERMS  # noqa: E402,F401

ROLES = {
    "Admin": ("Barcha imkoniyatlar", list(PERMS)),
    "Operator": ("Fayl yuklaydi, ko‘radi, Excel oladi va Telegramga yuboradi",
                 ["view_dashboard", "export_excel", "upload_pdf", "view_monitoring", "upload_monitoring", "send_monitoring", "view_location"]),
    "Tahlilchi": ("Ko‘radi va Excel oladi", ["view_dashboard", "export_excel", "view_monitoring", "view_location"]),
    "Kuzatuvchi": ("Faqat ko‘radi", ["view_dashboard", "view_monitoring"]),
}
# Mavjud rollarga yangilanishda avtomatik qo‘shiladigan ruxsatlar (qolganini administrator o‘zi belgilaydi)
AUTO_ADD = {"view_location"}


def sync_catalog():
    """Hudud, bo‘lim va ko‘rsatkichlarni katalogdan bazaga yozadi (qayta chaqirish xavfsiz)."""
    for i, (key, name, _aliases) in enumerate(DISTRICTS):
        District.objects.update_or_create(key=key, defaults={"name": name, "sort_order": i + 1, "is_total": False})
    District.objects.update_or_create(key=TOTAL_KEY, defaults={"name": TOTAL_NAME, "sort_order": 0, "is_total": True})
    for si, sdef in enumerate(SECTIONS):
        section, _ = Section.objects.update_or_create(
            key=sdef.key, defaults={"title": sdef.title, "short": sdef.short, "sort_order": si}
        )
        for mi, m in enumerate(sdef.metrics):
            Metric.objects.update_or_create(
                section=section,
                key=m.key,
                defaults={"label": m.label, "group": m.group, "kind": m.kind, "additive": m.additive, "sort_order": mi},
            )


def sync_roles():
    from django.contrib.auth.management import create_permissions

    create_permissions(django_apps.get_app_config("reports"), verbosity=0)
    perms = {p.codename: p for p in Permission.objects.filter(content_type__app_label="reports", codename__in=PERMS)}
    for role, (_desc, codes) in ROLES.items():
        group, created = Group.objects.get_or_create(name=role)
        if created or role == "Admin":
            group.permissions.set([perms[c] for c in codes if c in perms])
        else:  # yangi bo‘lim ruxsatlari mavjud rollarga ham qo‘shiladi (mavjudlari o‘zgarmaydi)
            group.permissions.add(*[perms[c] for c in codes if c in perms and (c.endswith("_monitoring") or c in AUTO_ADD)])


def audit(user, action: str, detail: str = ""):
    AuditLog.objects.create(
        user=user if getattr(user, "pk", None) else None,
        username=getattr(user, "username", "") or "",
        action=action,
        detail=detail[:4000],
    )


# ------------------------------------------------------------------ yuklash: vaqtinchalik saqlash
PENDING_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def pending_dir() -> Path:
    d = Path(settings.MEDIA_ROOT) / "pending"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pending_path(token: str) -> Path | None:
    if not PENDING_TOKEN.match(token or ""):
        return None
    p = pending_dir() / f"{token}.pdf"
    return p if p.exists() else None


def stash_upload(uploaded) -> tuple[str, Path]:
    """Yuklangan faylni vaqtincha saqlaydi. Eski (24 soatdan oshgan) fayllarni tozalaydi."""
    now = time.time()
    for old in list(pending_dir().glob("*.pdf")) + list(pending_dir().glob("*.pkl")):
        if now - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
    token = uuid.uuid4().hex
    path = pending_dir() / f"{token}.pdf"
    with open(path, "wb") as fh:
        for chunk in uploaded.chunks():
            fh.write(chunk)
    return token, path


def parsed_path(token: str) -> Path:
    return pending_dir() / f"{token}.pkl"


def save_parsed(token: str, rep) -> None:
    import pickle

    with open(parsed_path(token), "wb") as fh:
        pickle.dump(rep, fh, protocol=pickle.HIGHEST_PROTOCOL)


def load_parsed(token: str):
    import pickle

    if not PENDING_TOKEN.match(token or ""):
        return None
    try:
        with open(parsed_path(token), "rb") as fh:
            return pickle.load(fh)
    except (OSError, pickle.PickleError, EOFError):
        return None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def db_lookup_factory(prev_date: date):
    """Parser tekshiruvlari uchun: oldingi sana qiymatlarini bazadan o‘qiydigan funksiya."""
    rows = MetricValue.objects.filter(report_date=prev_date).values_list(
        "metric__section__key", "district__key", "metric__key", "value"
    )
    cache = {(s, d, m): v for s, d, m, v in rows}
    return lambda s, d, m, _date: cache.get((s, d, m))


def analyse_file(path: Path, progress=None) -> ParsedReport:
    """PDF ni o‘qiydi, ichki va bazadagi ma’lumotlar bilan tekshiradi.

    progress(bosqich, a, b): «read» — a/b sahifa; «check» — tekshiruv boshlandi."""
    rep = parse_pdf(path, (lambda a, b: progress("read", a, b)) if progress else None)
    if progress:
        progress("check", 0, 0)
    rep = validate(rep)
    if rep.ok and rep.prev_date:
        rep.warnings.extend(check_against_db(rep, db_lookup_factory(rep.prev_date)))
    return rep


class DuplicateUpload(Exception):
    def __init__(self, existing: Upload):
        super().__init__(f"{existing.report_date:%d.%m.%Y} uchun PDF allaqachon yuklangan.")
        self.existing = existing


# ------------------------------------------------------------------ saqlash
@transaction.atomic
def save_report(rep: ParsedReport, path: Path, original_name: str, user, replace: bool = False) -> Upload:
    if not rep.ok:
        raise ValueError("Xatoli hisobotni saqlab bo‘lmaydi.")

    existing = Upload.objects.filter(report_date=rep.report_date).first()
    if existing:
        if not replace:
            raise DuplicateUpload(existing)
        delete_upload(existing, user, reason="almashtirildi")

    digest = file_sha256(path)
    upload = Upload(
        report_date=rep.report_date,
        prev_date=rep.prev_date,
        variant=rep.variant,
        pages=rep.pages,
        original_name=original_name[:255],
        file_hash=digest,
        uploaded_by=user if getattr(user, "pk", None) else None,
        warnings=rep.warnings[:200],
    )
    with open(path, "rb") as fh:
        upload.file.save(f"{rep.report_date:%Y-%m-%d}_{digest[:8]}.pdf", File(fh), save=False)
    upload.save()

    districts = {d.key: d for d in District.objects.all()}
    metrics = {(m.section.key, m.key): m for m in Metric.objects.select_related("section")}

    # Shu sana uchun keyingi kun PDF idan olingan «oldingi holat» qiymatlari bo‘lsa — o‘zimizniki ustun
    MetricValue.objects.filter(report_date=rep.report_date, is_prev=True).delete()

    rows = []
    for skey, sd in rep.sections.items():
        for dkey, vals in sd.cur.items():
            for mkey, v in vals.items():
                rows.append(MetricValue(upload=upload, report_date=rep.report_date, district=districts[dkey],
                                        metric=metrics[(skey, mkey)], value=v, is_prev=False))
    have_prev = set(
        MetricValue.objects.filter(report_date=rep.prev_date).values_list("district_id", "metric_id")
    )
    for skey, sd in rep.sections.items():
        for dkey, vals in sd.prev.items():
            for mkey, v in vals.items():
                d, m = districts[dkey], metrics[(skey, mkey)]
                if (d.id, m.id) not in have_prev:
                    rows.append(MetricValue(upload=upload, report_date=rep.prev_date, district=d, metric=m,
                                            value=v, is_prev=True))
    MetricValue.objects.bulk_create(rows, batch_size=1000)
    audit(user, "upload_replace" if existing else "upload",
          f"{rep.report_date:%d.%m.%Y} · {original_name} · {len(rows)} ta qiymat · ogohlantirish: {len(rep.warnings)}")
    return upload


@transaction.atomic
def delete_upload(upload: Upload, user, reason: str = "o‘chirildi"):
    label = f"{upload.report_date:%d.%m.%Y} · {upload.original_name}"
    storage_name = upload.file.name
    upload.values.all().delete()
    upload.delete()
    if storage_name:
        upload.file.storage.delete(storage_name)
    audit(user, "upload_delete", f"{label} ({reason})")


# ------------------------------------------------------------------ sanalar
def available_dates():
    """[(sana, o‘z PDF i bormi)] — yangisi birinchi."""
    own = set(Upload.objects.values_list("report_date", flat=True))
    dates = set(MetricValue.objects.values_list("report_date", flat=True).distinct()) | own
    return [(d, d in own) for d in sorted(dates, reverse=True)]


def parse_iso(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ solishtirish
def quick_ranges(dates) -> list:
    """Tez tanlov tugmalari: [{label, a, b}]. `dates` — kamayish tartibida."""
    if len(dates) < 2:
        return []
    latest = dates[0]
    out = [{"label": "Oxirgi kun", "a": dates[1], "b": latest}]
    week = next((d for d in dates if d <= latest - timedelta(days=7)), None)
    if week:
        out.append({"label": "7 kunlik", "a": week, "b": latest})
    month_first = min((d for d in dates if (d.year, d.month) == (latest.year, latest.month)), default=None)
    if month_first and month_first != latest and month_first != dates[1]:
        out.append({"label": "Oy boshidan", "a": month_first, "b": latest})
    return out


def build_comparison(a: date, b: date, section_keys=None) -> dict:
    """Ikki sana (A -> B) bo‘yicha barcha bo‘limlarning solishtirma jadvali."""
    districts = list(District.objects.all())
    sections = list(Section.objects.prefetch_related("metrics"))
    if section_keys:
        sections = [s for s in sections if s.key in section_keys]

    data = defaultdict(dict)  # (skey, dkey, mkey) -> {a: .., b: ..}
    qs = MetricValue.objects.filter(report_date__in=[a, b]).values_list(
        "metric__section__key", "district__key", "metric__key", "report_date", "value"
    )
    for skey, dkey, mkey, d, v in qs:
        data[(skey, dkey, mkey)]["a" if d == a else "b"] = v

    out_sections = []
    for sec in sections:
        metrics = [
            m for m in sec.metrics.all()
            if any(("a" in data.get((sec.key, d.key, m.key), {}) or "b" in data.get((sec.key, d.key, m.key), {}))
                   for d in districts)
        ]
        if not metrics:
            continue
        groups = []
        for m in metrics:
            if groups and groups[-1]["name"] == m.group:
                groups[-1]["metrics"].append(m)
            else:
                groups.append({"name": m.group, "metrics": [m]})
        rows = []
        for d in districts:
            cells = []
            for m in metrics:
                cell = data.get((sec.key, d.key, m.key), {})
                va, vb = cell.get("a"), cell.get("b")
                diff = (vb - va) if (va is not None and vb is not None) else None
                cells.append({"a": va, "b": vb, "diff": diff, "kind": m.kind})
            rows.append({"district": d, "is_total": d.is_total, "cells": cells})
        out_sections.append({"section": sec, "metrics": metrics, "groups": groups, "rows": rows})
    return {"a": a, "b": b, "sections": out_sections}


# ------------------------------------------------------------------ dashboard
KPIS = (
    ("konsentratorlar", "dcu_online", "Online konsentratorlar"),
    ("konsentratorlar", "dcu_offline", "Offline konsentratorlar"),
    ("aloqasiz", "nome_all", "Aloqaga chiqmagan hisoblagichlar"),
    ("aloqasiz", "nome_pct", "Aloqasizlar ulushi"),
    ("uzilgan", "reloff_all", "Tarmoqdan uzilgan hisoblagichlar"),
    ("obxod48", "all_pct", "48 soatda obxod bergan"),
    ("billing_tafovut", "bill_total", "Billing bilan almashmayotgan"),
    ("konsentratorlar", "dcu_p80_100", "Obxod 80–100% bo‘lgan konsentratorlar"),
)


def dashboard_data() -> dict | None:
    last = Upload.objects.order_by("-report_date").first()
    if not last:
        return None
    latest = last.report_date
    dates_all = [d for d, _ in available_dates()]
    prev = next((d for d in dates_all if d < latest), None)
    since = latest - timedelta(days=30)

    metrics = {(m.section.key, m.key): m for m in Metric.objects.select_related("section")}
    wanted = [metrics[(s, m)] for s, m, _ in KPIS if (s, m) in metrics]
    series = defaultdict(dict)
    for mid, d, v in MetricValue.objects.filter(
        district__key=TOTAL_KEY, metric__in=wanted, report_date__gte=since, report_date__lte=latest
    ).values_list("metric_id", "report_date", "value"):
        series[mid][d] = v

    cards = []
    for skey, mkey, label in KPIS:
        m = metrics.get((skey, mkey))
        if not m:
            continue
        s = series.get(m.id, {})
        cur = s.get(latest)
        old = s.get(prev) if prev else None
        pts = [float(s[d]) for d in sorted(s)][-14:]
        cards.append({
            "label": label, "kind": m.kind, "value": cur,
            "diff": (cur - old) if (cur is not None and old is not None) else None,
            "spark": pts, "section": skey, "metric": mkey,
        })

    movers = []
    if prev:
        comp = build_comparison(prev, latest, section_keys={"aloqasiz"})
        if comp["sections"]:
            sec = comp["sections"][0]
            idx = next((i for i, m in enumerate(sec["metrics"]) if m.key == "nome_all"), None)
            if idx is not None:
                for row in sec["rows"]:
                    c = row["cells"][idx]
                    if not row["is_total"] and c["diff"] is not None:
                        movers.append({"district": row["district"].name, "a": c["a"], "b": c["b"], "diff": c["diff"]})
                movers.sort(key=lambda x: abs(x["diff"]), reverse=True)
                movers = movers[:6]

    own = sorted(Upload.objects.filter(report_date__gte=since).values_list("report_date", flat=True))
    gaps = []
    if own:
        day, end = own[0], latest
        have = set(own)
        while day <= end:
            if day not in have:
                gaps.append(day)
            day += timedelta(days=1)
    return {
        "latest": latest, "prev": prev, "cards": cards, "movers": movers, "gaps": gaps,
        "uploads_count": Upload.objects.count(), "last_upload": last,
    }


# ------------------------------------------------------------------ trend
def trend_data(section_key: str, metric_key: str, district_keys, d_from: date, d_to: date) -> dict:
    metric = Metric.objects.select_related("section").filter(section__key=section_key, key=metric_key).first()
    if not metric:
        return {"dates": [], "series": [], "kind": "count", "label": ""}
    district_keys = [k for k in district_keys if k in DISTRICT_KEYS or k == TOTAL_KEY] or [TOTAL_KEY]
    qs = MetricValue.objects.filter(
        metric=metric, district__key__in=district_keys, report_date__gte=d_from, report_date__lte=d_to
    ).values_list("district__key", "district__name", "report_date", "value")
    names, by = {}, defaultdict(dict)
    dates = set()
    for dk, dn, d, v in qs:
        names[dk] = dn
        by[dk][d] = float(v)
        dates.add(d)
    dates = sorted(dates)
    series = [{"name": names[k], "data": [by[k].get(d) for d in dates]} for k in district_keys if k in by]
    return {
        "dates": [d.isoformat() for d in dates], "series": series,
        "kind": metric.kind, "label": f"{metric.section.short}: {metric.label}",
    }
