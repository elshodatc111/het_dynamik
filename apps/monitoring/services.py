"""Offline monitoring biznes-mantiqi: sozlamalar, davrlarga bo‘lish, saqlash, svod hisoblash."""
from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from django.conf import settings as dj_settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import GROUPS, DcuRecord, MeterRecord, MonitorSetting, Snapshot
from .parsing import Parsed, parse_file

GROUP_KEYS = [g for g, _ in GROUPS]
CLASSIFY_ORDER = ("balans", "yuridik", "aholi")  # «Индикатор баланса» avval tekshiriladi


# ------------------------------------------------------------------ sozlamalar
def get_settings() -> MonitorSetting:
    obj, _ = MonitorSetting.objects.get_or_create(pk=1)
    return obj


def split_words(text: str) -> list[str]:
    out, seen = [], set()
    for w in re.split(r"[,;\n]+", text or ""):
        w = w.strip().lower()
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def parse_edges(text: str, label: str = "Chegaralar") -> list[int]:
    nums = re.findall(r"\d+", text or "")
    edges = [int(n) for n in nums]
    if len(edges) < 2:
        raise ValueError(f"{label}: kamida ikkita son kerak (masalan 2, 7, 30).")
    if len(edges) > 8:
        raise ValueError(f"{label}: 8 tadan ortiq chegara mumkin emas.")
    if edges[0] < 1:
        raise ValueError(f"{label}: birinchi chegara 1 dan kichik bo‘lmasin.")
    if any(b <= a for a, b in zip(edges, edges[1:])):
        raise ValueError(f"{label}: sonlar o‘sish tartibida va takrorlanmasdan yozilsin.")
    return edges


def dcu_edges(cfg: MonitorSetting | None = None) -> list[int]:
    return parse_edges((cfg or get_settings()).dcu_edges, "Konsentrator chegaralari")


def meter_edges(cfg: MonitorSetting | None = None) -> list[int]:
    return parse_edges((cfg or get_settings()).meter_edges, "Hisoblagich chegaralari")


def bucket_index(days: int, edges: list[int]):
    """None — birinchi chegaradan kam. Oraliqlar: [e0..e1], (e1..e2], ..., (e_last..∞)."""
    if days < edges[0]:
        return None
    for i in range(1, len(edges)):
        if days <= edges[i]:
            return i - 1
    return len(edges) - 1


def bucket_labels(edges: list[int]) -> list[str]:
    labels = [f"{edges[i]}–{edges[i + 1]} kun" for i in range(len(edges) - 1)]
    labels.append(f"{edges[-1]}+ kun")
    return labels


def bucket_q(edges: list[int], i: int, field: str = "days") -> Q:
    last = len(edges) - 1
    if i == 0:
        return Q(**{f"{field}__gte": edges[0], f"{field}__lte": edges[1]})
    if i >= last:
        return Q(**{f"{field}__gt": edges[last]})
    return Q(**{f"{field}__gt": edges[i], f"{field}__lte": edges[i + 1]})


def keyword_map(cfg: MonitorSetting) -> dict[str, list[str]]:
    return {"yuridik": split_words(cfg.kw_yuridik), "aholi": split_words(cfg.kw_aholi), "balans": split_words(cfg.kw_balans)}


def classify(raw: str, kw: dict[str, list[str]]) -> str:
    s = (raw or "").lower()
    if s:
        for g in CLASSIFY_ORDER:
            if any(k in s for k in kw[g]):
                return g
    return "boshqa"


# ------------------------------------------------------------------ vaqtinchalik fayllar
_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def pending_dir() -> Path:
    d = Path(dj_settings.MEDIA_ROOT) / "pending_mon"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pending_path(token: str) -> Path | None:
    if not _TOKEN.match(token or ""):
        return None
    p = pending_dir() / f"{token}.xlsx"
    return p if p.exists() else None


def stash_upload(uploaded) -> tuple[str, Path]:
    now = time.time()
    for old in list(pending_dir().glob("*.xlsx")) + list(pending_dir().glob("*.pkl")):
        if now - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
    token = uuid.uuid4().hex
    path = pending_dir() / f"{token}.xlsx"
    with open(path, "wb") as fh:
        for chunk in uploaded.chunks():
            fh.write(chunk)
    return token, path


def analyse(path: Path, name: str, progress=None) -> Parsed:
    return parse_file(path, name, split_words(get_settings().offline_keywords), progress)


def parsed_path(token: str) -> Path:
    return pending_dir() / f"{token}.pkl"


def save_parsed(token: str, res: Parsed):
    """Tahlil natijasini diskka yozadi — tasdiqlashda fayl qayta o‘qilmaydi."""
    import pickle

    with open(parsed_path(token), "wb") as fh:
        pickle.dump(res, fh, protocol=pickle.HIGHEST_PROTOCOL)


def load_parsed(token: str) -> Parsed | None:
    import pickle

    if not _TOKEN.match(token or ""):
        return None
    try:
        with open(parsed_path(token), "rb") as fh:
            return pickle.load(fh)
    except (OSError, pickle.PickleError, EOFError):
        return None


# ------------------------------------------------------------------ saqlash
class AlreadyUploaded(Exception):
    def __init__(self, snapshot: Snapshot, part: str):
        self.snapshot, self.part = snapshot, part


def _prev_dcu_snapshot(report_date: date):
    return (Snapshot.objects.filter(report_date__lt=report_date, dcu_uploaded_at__isnull=False)
            .order_by("-report_date").first())


def _assign_since(snapshot: Snapshot, records: list, use_file_time: bool):
    """Har bir konsentrator uchun offline boshlangan sana va kunlar sonini belgilaydi."""
    prev = _prev_dcu_snapshot(snapshot.report_date)
    prev_map = dict(DcuRecord.objects.filter(snapshot=prev).values_list("device_no", "offline_since")) if prev else {}
    rd = snapshot.report_date
    for r in records:
        since = prev_map.get(r.device_no)
        if since is None:
            since = rd
            if use_file_time and r.status_at:
                since = min(rd, timezone.localtime(r.status_at).date() if timezone.is_aware(r.status_at) else r.status_at.date())
        r.offline_since = since
        r.days = max(0, (rd - since).days)


def _rebuild_chain_after(report_date: date, use_file_time: bool, progress=None):
    later = list(Snapshot.objects.filter(report_date__gt=report_date, dcu_uploaded_at__isnull=False).order_by("report_date"))
    for i, snap in enumerate(later):
        if progress:
            progress("chain", i / len(later))
        recs = list(snap.dcus.all())
        _assign_since(snap, recs, use_file_time)
        DcuRecord.objects.bulk_update(recs, ["offline_since", "days"], batch_size=1000)
        snap.summary = compute_summary(snap)
        snap.save(update_fields=["summary"])


def _aware(dt):
    if dt is None:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


@transaction.atomic
def save_parts(report_date: date, dcu: Parsed | None, meters: Parsed | None, user, replace: bool = False, progress=None) -> Snapshot:
    """progress(bosqich, frac): bosqichlar — prepare, dcu, meters, chain, summary, purge."""
    pr = progress or (lambda stage, frac: None)
    pr("prepare", 0.0)
    cfg = get_settings()
    snap, created = Snapshot.objects.select_for_update().get_or_create(report_date=report_date)
    if not created and not replace:
        if dcu and snap.has_dcu:
            raise AlreadyUploaded(snap, "dcu")
        if meters and snap.has_meters:
            raise AlreadyUploaded(snap, "meters")
    now = timezone.now()
    warns = [w for w in (snap.warnings or []) if not (dcu and w.startswith("[DCU]")) and not (meters and w.startswith("[Hisoblagich]"))]
    snap.uploaded_by = user if getattr(user, "pk", None) else None

    pr("prepare", 1.0)
    if dcu:
        pr("dcu", 0.0)
        snap.dcus.all().delete()
        recs = [DcuRecord(snapshot=snap, offline_since=report_date, **{**row, "status_at": _aware(row["status_at"])}) for row in dcu.rows]
        _assign_since(snap, recs, cfg.use_file_time)
        for i in range(0, len(recs), 1000):
            DcuRecord.objects.bulk_create(recs[i:i + 1000])
            pr("dcu", min(1.0, (i + 1000) / max(len(recs), 1)))
        snap.dcu_name, snap.dcu_uploaded_at, snap.dcu_rows = dcu.name[:255], now, len(recs)
        warns += [f"[DCU] {w}" for w in dcu.warnings]

    if meters:
        pr("meters", 0.0)
        snap.meters.all().delete()
        kw = keyword_map(cfg)
        cache: dict[str, str] = {}
        recs = []
        for row in meters.rows:
            raw = row["consumer_type"]
            if raw not in cache:
                cache[raw] = classify(raw, kw)
            recs.append(MeterRecord(snapshot=snap, group=cache[raw], **row))
        pr("meters", 0.25)
        for i in range(0, len(recs), 2000):
            MeterRecord.objects.bulk_create(recs[i:i + 2000])
            pr("meters", 0.25 + 0.75 * min(1.0, (i + 2000) / max(len(recs), 1)))
        snap.meter_name, snap.meter_uploaded_at, snap.meter_rows = meters.name[:255], now, len(recs)
        snap.meter_detail_purged = False
        warns += [f"[Hisoblagich] {w}" for w in meters.warnings]

    snap.warnings = warns
    snap.save()
    if dcu:
        pr("chain", 0.0)
        _rebuild_chain_after(report_date, cfg.use_file_time, pr)
    pr("summary", 0.0)
    snap.summary = compute_summary(snap)
    snap.save(update_fields=["summary"])
    pr("purge", 0.0)
    purge_old_details(cfg)
    return snap


def purge_old_details(cfg: MonitorSetting | None = None):
    cfg = cfg or get_settings()
    cutoff = timezone.localdate() - timedelta(days=cfg.detail_keep_days)
    old = Snapshot.objects.filter(report_date__lt=cutoff, meter_detail_purged=False, meter_uploaded_at__isnull=False)
    for snap in old:
        snap.meters.all().delete()
        snap.meter_detail_purged = True
        snap.save(update_fields=["meter_detail_purged"])


def delete_snapshot(snap: Snapshot):
    rd = snap.report_date
    snap.delete()
    _rebuild_chain_after(rd, get_settings().use_file_time)


def recompute_groups(cfg: MonitorSetting | None = None):
    """Kalit so‘zlar o‘zgargach, saqlangan hisoblagichlarning turini qayta aniqlaydi."""
    cfg = cfg or get_settings()
    kw = keyword_map(cfg)
    for raw in MeterRecord.objects.order_by().values_list("consumer_type", flat=True).distinct():
        MeterRecord.objects.filter(consumer_type=raw).update(group=classify(raw, kw))
    refresh_summaries()


def refresh_summaries():
    for snap in Snapshot.objects.all():
        snap.summary = compute_summary(snap)
        snap.save(update_fields=["summary"])


# ------------------------------------------------------------------ svod
def compute_summary(snap: Snapshot) -> dict:
    cfg = get_settings()
    d_edges, m_edges = dcu_edges(cfg), meter_edges(cfg)
    out = {"dcu_edges": d_edges, "meter_edges": m_edges, "dcu": None, "meters": None}

    if snap.has_dcu:
        nb = len(d_edges)
        counts, by_esp = [0] * nb, defaultdict(lambda: [0] * nb)
        below = 0
        for days, esp in DcuRecord.objects.filter(snapshot=snap).values_list("days", "esp"):
            i = bucket_index(days, d_edges)
            if i is None:
                below += 1
                continue
            counts[i] += 1
            by_esp[esp or "—"][i] += 1
        total = snap.dcus.count()
        prev = _prev_dcu_snapshot(snap.report_date)
        new = recovered = None
        if prev:
            cur_ids = set(snap.dcus.values_list("device_no", flat=True))
            prev_ids = set(prev.dcus.values_list("device_no", flat=True))
            new, recovered = len(cur_ids - prev_ids), len(prev_ids - cur_ids)
        out["dcu"] = {
            "offline_total": total, "below_min": below, "over_min": sum(counts), "buckets": counts,
            "by_esp": dict(sorted(by_esp.items())), "new": new, "recovered": recovered,
            "prev_date": prev.report_date.isoformat() if prev else None,
        }

    if snap.has_meters and not snap.meter_detail_purged:
        nb = len(m_edges)
        matrix = [dict.fromkeys(GROUP_KEYS, 0) for _ in range(nb)]
        by_esp = defaultdict(lambda: [0] * nb)
        by_esp_group = defaultdict(lambda: dict.fromkeys(GROUP_KEYS, 0))
        esp_matrix = defaultdict(lambda: [dict.fromkeys(GROUP_KEYS, 0) for _ in range(nb)])
        below = 0
        rows = (MeterRecord.objects.filter(snapshot=snap).values("days", "group", "esp").annotate(n=Count("id")))
        for r in rows:
            i = bucket_index(r["days"], m_edges)
            if i is None:
                below += r["n"]
                continue
            esp = r["esp"] or "—"
            matrix[i][r["group"]] += r["n"]
            by_esp[esp][i] += r["n"]
            by_esp_group[esp][r["group"]] += r["n"]
            esp_matrix[esp][i][r["group"]] += r["n"]
        groups_total = {g: sum(m[g] for m in matrix) for g in GROUP_KEYS}
        out["meters"] = {
            "total": snap.meters.count(), "below_min": below, "over_min": sum(groups_total.values()),
            "matrix": matrix, "bucket_totals": [sum(m.values()) for m in matrix], "groups_total": groups_total,
            "by_esp": dict(sorted(by_esp.items())), "by_esp_group": dict(sorted(by_esp_group.items())),
            "esp_matrix": dict(sorted(esp_matrix.items())),
        }
    elif snap.has_meters and snap.summary and snap.summary.get("meters"):
        out["meters"] = snap.summary["meters"]  # ro‘yxat tozalangan — eski svod saqlanadi
    return out


def previous_snapshot(snap: Snapshot, part: str):
    qs = Snapshot.objects.filter(report_date__lt=snap.report_date).order_by("-report_date")
    qs = qs.filter(dcu_uploaded_at__isnull=False) if part == "dcu" else qs.filter(meter_uploaded_at__isnull=False)
    return qs.first()


def deltas(snap: Snapshot) -> dict:
    """Oldingi hisobotga nisbatan farqlar (None — taqqoslash mumkin emas)."""
    s = snap.summary or {}
    out = {"dcu_total": None, "dcu_buckets": None, "meter_total": None, "meter_buckets": None, "dcu_prev": None, "meter_prev": None}
    if s.get("dcu"):
        p = previous_snapshot(snap, "dcu")
        ps = (p.summary or {}).get("dcu") if p else None
        if ps and (p.summary or {}).get("dcu_edges") == s.get("dcu_edges"):
            out["dcu_prev"] = p.report_date
            out["dcu_total"] = s["dcu"]["over_min"] - ps["over_min"]
            out["dcu_buckets"] = [a - b for a, b in zip(s["dcu"]["buckets"], ps["buckets"])]
    if s.get("meters"):
        p = previous_snapshot(snap, "meters")
        ps = (p.summary or {}).get("meters") if p else None
        if ps and (p.summary or {}).get("meter_edges") == s.get("meter_edges"):
            out["meter_prev"] = p.report_date
            out["meter_total"] = s["meters"]["over_min"] - ps["over_min"]
            out["meter_buckets"] = [a - b for a, b in zip(s["meters"]["bucket_totals"], ps["bucket_totals"])]
    return out


def fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def fmt_delta(n) -> str:
    if n is None:
        return "—"
    return "–" if n == 0 else (f"↑ {fmt_int(n)}" if n > 0 else f"↓ {fmt_int(-n)}")


# ------------------------------------------------------------------ svod jadvali (matritsa)
REGION_NAME = getattr(dj_settings, "MONITORING_REGION", "Qashqadaryo HETK")
# shablondagi imlo: «Axoli» (foydalanuvchi Excel shabloniga mos)
GRID_GROUP_NAMES = {"aholi": "Axoli", "yuridik": "Yuridik", "balans": "Balans", "boshqa": "Boshqa"}


def esp_sort_key(name: str):
    return re.sub(r"[‘’ʻʼ'`]", "", name or "").casefold()


def ensure_summary(snap: Snapshot) -> Snapshot:
    """Eski svodlarda ESP×davr×guruh kesimi yo‘q — ro‘yxat saqlangan bo‘lsa qayta hisoblaydi."""
    m = (snap.summary or {}).get("meters")
    if m is not None and "esp_matrix" not in m and snap.has_meters and not snap.meter_detail_purged:
        snap.summary = compute_summary(snap)
        snap.save(update_fields=["summary"])
    return snap


def grid_for(snap: Snapshot, simple: bool = False) -> dict:
    """Svod jadvali: ustunlar va ESP qatorlari. Qiymat None — ma’lumot yo‘q (kesim hisoblanmagan)."""
    snap = ensure_summary(snap)
    s = snap.summary or {}
    dcu, mt = s.get("dcu"), s.get("meters")
    d_labels = bucket_labels(s.get("dcu_edges") or dcu_edges())
    m_labels = bucket_labels(s.get("meter_edges") or meter_edges())
    gkeys = ["aholi", "yuridik", "balans"] + (["boshqa"] if mt and mt["groups_total"].get("boshqa") else [])
    if simple:  # solishtirish: faqat umumiy sonlar (davrlar bo‘yicha), iste’molchi guruhlarisiz
        gkeys = []
        d_labels_all, d_labels = d_labels, []
    cols = []
    if dcu:
        cols += [{"sec": "dcu", "b": None, "g": None, "sep": True}] + [{"sec": "dcu", "b": i, "g": None, "sep": False} for i in range(len(d_labels))]
    if mt:
        cols.append({"sec": "m", "b": None, "g": None, "sep": True})
        for i in range(len(m_labels)):
            cols.append({"sec": "m", "b": i, "g": None, "sep": True})
            cols += [{"sec": "m", "b": i, "g": g, "sep": False} for g in gkeys]
    esps = sorted(set((dcu or {}).get("by_esp", {})) | set((mt or {}).get("by_esp", {})), key=esp_sort_key)

    def val(esp, c):
        if c["sec"] == "dcu":
            lst = (dcu["by_esp"].get(esp) if esp is not None else None)
            if esp is None:
                return sum(sum(v) for v in dcu["by_esp"].values()) if c["b"] is None else sum(v[c["b"]] for v in dcu["by_esp"].values())
            if lst is None:
                return 0
            return sum(lst) if c["b"] is None else lst[c["b"]]
        b, g = c["b"], c["g"]
        em = mt.get("esp_matrix")
        if esp is None:
            esp_list = list(mt["by_esp"])
        else:
            esp_list = [esp]
        total = 0
        for e in esp_list:
            if g is None:
                lst = mt["by_esp"].get(e)
                if lst is None:
                    continue
                total += sum(lst) if b is None else lst[b]
            elif b is None:
                total += mt["by_esp_group"].get(e, {}).get(g, 0)
            else:
                if em is None:
                    return None
                total += em.get(e, [{}] * len(m_labels))[b].get(g, 0)
        return total

    rows = [{"esp": e, "vals": [val(e, c) for c in cols]} for e in esps]
    total = {"esp": "JAMI", "vals": [val(None, c) for c in cols]}
    return {"cols": cols, "rows": rows, "total": total, "d_labels": d_labels, "m_labels": m_labels, "gkeys": gkeys,
            "gnames": {g: GRID_GROUP_NAMES[g] for g in gkeys}, "gnames_list": [GRID_GROUP_NAMES[g] for g in gkeys],
            "has_dcu": bool(dcu), "has_meters": bool(mt), "snap": snap, "simple": simple, "hrows": 2 if simple else 3,
            "colspan_d": 1 + len(d_labels), "span_b": 1 + len(gkeys), "colspan_m": 1 + len(m_labels) * (1 + len(gkeys)),
            "d_min": (s.get("dcu_edges") or dcu_edges())[0], "m_min": (s.get("meter_edges") or meter_edges())[0]}


def compare_grids(a: Snapshot, b: Snapshot) -> dict:
    """B (keyingi) sanani A (oldingi) bilan yacheykama-yacheyka solishtiradi."""
    ga, gb = grid_for(a, simple=True), grid_for(b, simple=True)
    sa, sb = a.summary or {}, b.summary or {}
    ok = {
        "dcu": bool(ga["has_dcu"] and gb["has_dcu"] and sa.get("dcu_edges") == sb.get("dcu_edges")),
        "m": bool(ga["has_meters"] and gb["has_meters"] and sa.get("meter_edges") == sb.get("meter_edges")),
    }
    keys = [(c["sec"], c["b"], c["g"]) for c in gb["cols"]]
    a_key = [(c["sec"], c["b"], c["g"]) for c in ga["cols"]]

    def index(g, cols):
        out = {None: dict(zip(cols, g["total"]["vals"]))}
        for r in g["rows"]:
            out[r["esp"]] = dict(zip(cols, r["vals"]))
        return out

    ia, ib = index(ga, a_key), index(gb, keys)
    esps = sorted(set(ia) | set(ib) - {None}, key=lambda e: esp_sort_key(e or ""))
    esps = [e for e in esps if e is not None]
    rows = []

    def cell(esp, k):
        sec_ok = ok[k[0]]
        bv = ib.get(esp, {}).get(k, 0)
        av = (ia.get(esp, {}).get(k, 0) if sec_ok else None)
        if bv is None or av is None:
            d = None
        else:
            d = bv - av
        return {"b": bv, "a": av, "d": d}

    for e in esps:
        cells = [cell(e, k) for k in keys]
        rows.append({"esp": e, "cells": cells, "changed": any(c["d"] for c in cells)})
    total = {"esp": "JAMI", "cells": [cell(None, k) for k in keys], "changed": False}
    return {
        "grid": gb, "rows": rows, "total": total, "ok": ok,
        "b_rows": [{"esp": r["esp"], "vals": [c["b"] for c in r["cells"]]} for r in rows],
        "a_rows": [{"esp": r["esp"], "vals": [c["a"] for c in r["cells"]]} for r in rows],
        "diff_rows": [{"esp": r["esp"], "vals": [c["d"] for c in r["cells"]]} for r in rows],
        "b_total": {"esp": "JAMI", "vals": [c["b"] for c in total["cells"]]},
        "a_total": {"esp": "JAMI", "vals": [c["a"] for c in total["cells"]]},
        "diff_total": {"esp": "JAMI", "vals": [c["d"] for c in total["cells"]]},
    }


def compare_table(cmp: dict, a: Snapshot, b: Snapshot) -> dict:
    """Solishtirish jadvali modeli (veb, Excel, PDF uchun bir xil): har bir ko‘rsatkich — A sana | B sana | Farq."""
    g = cmp["grid"]
    da, db = f"{a.report_date:%d.%m}", f"{b.report_date:%d.%m}"
    dcu_cols = [c for c in g["cols"] if c["sec"] == "dcu"]
    m_cols = [c for c in g["cols"] if c["sec"] == "m"]
    head1 = [("ESP", 1, 3)]
    if dcu_cols:
        head1.append(("Offline konsentratorlar", 3 * len(dcu_cols), 1))
    if m_cols:
        head1.append(("Aloqadan chiqib ketgan hisoblagichlar", 3 * len(m_cols), 1))
    head2, head3 = [], []
    for c in g["cols"]:
        labels = g["d_labels"] if c["sec"] == "dcu" else g["m_labels"]
        head2.append(("JAMI" if c["b"] is None else labels[c["b"]], 3, 1))
        head3 += [(da, 1, 1), (db, 1, 1), ("Farq", 1, 1)]
    sep = []  # har bir ko‘rsatkich blokining boshlanish ustuni (ESP ustunidan keyin)
    for i in range(len(g["cols"])):
        sep.append(1 + 3 * i)
    return {"head": [head1, head2, head3], "sep_cols": sep, "rows": cmp["rows"], "total": cmp["total"], "ncols": len(g["cols"]),
            "da": da, "db": db}
