from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import urlencode

from django.conf import settings as dj_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import jobs
from apps.reports.services import audit

from . import exports, image, pdfs, sender, services, tasks, telegram
from .models import GROUPS, DcuRecord, MeterRecord, SendLog, Snapshot

view_perm = permission_required("reports.view_monitoring", raise_exception=True)
upload_perm = permission_required("reports.upload_monitoring", raise_exception=True)
send_perm = permission_required("reports.send_monitoring", raise_exception=True)
manage_perm = permission_required("reports.manage_monitoring", raise_exception=True)
export_perm = permission_required("reports.export_excel", raise_exception=True)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ------------------------------------------------------------------ umumiy svod (matritsa)
def _grid_ctx(snap: Snapshot) -> dict:
    """Svod jadvali + har bir yacheyka uchun nomma-nom ro‘yxatga havola."""
    g = services.grid_for(snap)
    snap = g["snap"]
    dcu_ok = snap.has_dcu
    m_ok = snap.has_meters and not snap.meter_detail_purged

    def link(esp, c, v):
        if not v:
            return None
        q = {}
        if esp is not None:
            q["esp"] = esp
        if c["b"] is not None:
            q["b"] = c["b"]
        if c["sec"] == "dcu":
            if not dcu_ok:
                return None
            name = "mon_dcu"
        else:
            if not m_ok:
                return None
            name = "mon_meters"
            if c["g"]:
                q["g"] = c["g"]
        return reverse(name, args=[snap.pk]) + (f"?{urlencode(q)}" if q else "")

    def cells(row, esp):
        return [{"v": v, "url": link(esp, c, v), "sep": c["sep"]} for c, v in zip(g["cols"], row["vals"])]

    g["rows_c"] = [{"esp": r["esp"], "cells": cells(r, r["esp"])} for r in g["rows"]]
    g["total_c"] = {"esp": "JAMI", "cells": cells(g["total"], None)}
    return g


def _svod(request, snap: Snapshot | None):
    snaps = list(Snapshot.objects.order_by("-report_date").only("pk", "report_date"))
    ctx = {"snap": snap, "snaps": snaps, "region": services.REGION_NAME}
    if snap:
        pos = next((k for k, x in enumerate(snaps) if x.pk == snap.pk), 0)
        ctx.update(newer=snaps[pos - 1] if pos > 0 else None, older=snaps[pos + 1] if pos + 1 < len(snaps) else None,
                   is_latest=pos == 0, g=_grid_ctx(snap))
        snap = ctx["g"]["snap"]
        ctx["snap"] = snap
    return render(request, "monitoring/svod.html", ctx)


@login_required
@view_perm
def dashboard(request):
    return _svod(request, Snapshot.objects.order_by("-report_date").first())


@login_required
@view_perm
def snapshot(request, pk):
    return _svod(request, get_object_or_404(Snapshot, pk=pk))


@login_required
@view_perm
def archive(request):
    return render(request, "monitoring/archive.html", {"snaps": Snapshot.objects.select_related("uploaded_by")})


# ------------------------------------------------------------------ yuklash
def _kind_title(kind):
    return {"dcu": "Offline konsentratorlar", "meters": "Aloqasiz hisoblagichlar"}.get(kind, "Noma’lum")


@login_required
@upload_perm
def upload(request):
    """Fayllarni qabul qilib saqlaydi va tekshirishni orqa fonda boshlaydi (jonli progress bilan)."""
    if request.method != "POST":
        return render(request, "monitoring/upload.html", {})
    back = lambda: redirect("mon_upload")  # noqa: E731
    files = request.FILES.getlist("files")
    if not files:
        return jobs.reject(request, "Excel fayl tanlang.", back)
    if len(files) > 2:
        return jobs.reject(request, "Bir vaqtda ko‘pi bilan ikkita fayl (konsentratorlar va hisoblagichlar) yuklang.", back)
    items = []
    for f in files:
        token, path = services.stash_upload(f)
        items.append((token, str(path), f.name))
    job = jobs.create("mon_analyse", request.user, tasks.analyse_steps([n for _, _, n in items]))
    job.d["redirect"] = reverse("mon_upload_result", args=[job.id])
    return jobs.launch(request, job, tasks.analyse_job, items, fail_url=reverse("mon_upload"))


@login_required
@upload_perm
def upload_result(request, job_id):
    """Tekshiruv natijasi (job tugagach)."""
    data = jobs.load(job_id, request.user)
    if not data or data["status"] != "done" or data.get("kind") != "mon_analyse":
        messages.error(request, "Natija topilmadi yoki muddati o‘tgan. Fayllarni qaytadan yuklang.")
        return redirect("mon_upload")
    cards = data["result"]["cards"]
    pending = {c["token"]: {"name": c["name"], "kind": c["kind"]} for c in cards if c["ok"]}
    request.session["mon_pending"] = pending
    hint = data["result"].get("hint")
    report_date = date.fromisoformat(hint) if hint else timezone.localdate()
    for c in cards:
        c["res"] = {"ok": c["ok"], "errors": c["errors"], "warnings": c["warnings"]}
    existing = Snapshot.objects.filter(report_date=report_date).first()
    return render(request, "monitoring/upload_preview.html", {
        "cards": cards, "report_date": report_date, "existing": existing, "has_ok": bool(pending),
        "can_replace": request.user.has_perm("reports.manage_monitoring"),
    })


@login_required
@upload_perm
@require_POST
def upload_confirm(request):
    back = lambda: redirect("mon_upload")  # noqa: E731
    pending = request.session.get("mon_pending", {})
    tokens = [t for t in request.POST.getlist("token") if t in pending]
    if not tokens:
        return jobs.reject(request, "Saqlash uchun fayl tanlanmadi.", back)
    try:
        report_date = date.fromisoformat(request.POST.get("report_date", ""))
    except ValueError:
        return jobs.reject(request, "Hisobot sanasi noto‘g‘ri.", back)
    if report_date > timezone.localdate() + timedelta(days=1):
        return jobs.reject(request, "Hisobot sanasi kelajakda bo‘la olmaydi.", back)
    kinds = {pending[t]["kind"] for t in tokens}
    replace = bool(request.POST.get("replace")) and request.user.has_perm("reports.manage_monitoring")
    existing = Snapshot.objects.filter(report_date=report_date).first()
    if existing and not replace:
        for kind, has in (("dcu", existing.has_dcu), ("meters", existing.has_meters)):
            if kind in kinds and has:
                what = "konsentratorlar" if kind == "dcu" else "hisoblagichlar"
                return jobs.reject(request, f"{report_date:%d.%m.%Y} uchun {what} allaqachon yuklangan. "
                                            "Almashtirish uchun administrator ruxsati kerak.", back)
    steps, keys = tasks.save_plan("dcu" in kinds, "meters" in kinds)
    job = jobs.create("mon_save", request.user, steps, extra={"stage_keys": keys})
    # Saqlangach — svod sahifasi (snapshot pk ni job natijasidan olamiz)
    job.d["redirect"] = reverse("mon_snapshot_by_date", args=[report_date.isoformat()]) + "?saved=1"
    request.session["mon_pending"] = {}
    return jobs.launch(request, job, tasks.save_job, tokens, report_date.isoformat(), request.user.pk, replace, fail_url=reverse("mon_upload"))


@login_required
@view_perm
def snapshot_by_date(request, day):
    snap = Snapshot.objects.filter(report_date=day).first()
    if request.GET.get("saved"):
        messages.success(request, f"{day} uchun ma’lumotlar saqlandi.")
    return redirect("mon_snapshot", pk=snap.pk) if snap else redirect("mon_dashboard")


# ------------------------------------------------------------------ nomma-nom ro‘yxatlar
def _esp_filter(qs, esp: str):
    if esp:
        qs = qs.filter(esp="" if esp == "—" else esp)
    return qs


def _pdf_response(request, data: bytes, name: str, detail: str):
    audit(request.user, "mon_export", ("PDF: " + detail)[:3900])
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


def _xlsx_response(request, data: bytes, name: str, detail: str):
    audit(request.user, "mon_export", detail[:3900])
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@login_required
@view_perm
def dcu_list(request, pk):
    snap = get_object_or_404(Snapshot, pk=pk)
    edges = services.dcu_edges()
    labels = services.bucket_labels(edges)
    qs = DcuRecord.objects.filter(snapshot=snap, days__gte=edges[0])
    b = request.GET.get("b", "")
    esp = request.GET.get("esp", "")
    bi = int(b) if b.isdigit() and int(b) < len(labels) else None
    if bi is not None:
        qs = qs.filter(services.bucket_q(edges, bi))
    qs = _esp_filter(qs, esp)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(device_no__icontains=q) | Q(esp__icontains=q) | Q(substation__icontains=q) | Q(feeder__icontains=q)
                       | Q(tp__icontains=q) | Q(sim__icontains=q) | Q(manager__icontains=q))
    qs = qs.order_by("-days", "esp", "substation", "feeder", "device_no")
    if request.GET.get("pdf"):
        if not request.user.has_perm("reports.export_excel"):
            raise PermissionDenied
        name = exports.list_filename("konsentratorlar", snap, esp, labels[bi] if bi is not None else "").replace(".xlsx", ".pdf")
        desc = " · ".join(x for x in (esp, labels[bi] if bi is not None else "") if x) or "Hammasi"
        data, _cut = pdfs.dcu_list_pdf(qs, snap, desc)
        return _pdf_response(request, data, name, f"{snap.report_date:%d.%m.%Y} · {name}")
    if request.GET.get("xlsx"):
        if not request.user.has_perm("reports.export_excel"):
            raise PermissionDenied
        name = exports.list_filename("konsentratorlar", snap, esp, labels[bi] if bi is not None else "")
        return _xlsx_response(request, exports.filtered_dcu_xlsx(qs), name, f"{snap.report_date:%d.%m.%Y} · {name}")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    esps = sorted(set(DcuRecord.objects.filter(snapshot=snap).values_list("esp", flat=True)), key=services.esp_sort_key)
    desc = " · ".join(x for x in (esp, labels[bi] if bi is not None else "") if x) or "Hammasi"
    return render(request, "monitoring/dcu_list.html", {"snap": snap, "page": page, "labels": labels, "b": b, "q": q, "esp": esp,
                                                         "esps": [e or "—" for e in esps], "d_min": edges[0], "desc": desc})


@login_required
@view_perm
def meter_list(request, pk):
    snap = get_object_or_404(Snapshot, pk=pk)
    edges = services.meter_edges()
    labels = services.bucket_labels(edges)
    qs = MeterRecord.objects.filter(snapshot=snap, days__gte=edges[0])
    b, g = request.GET.get("b", ""), request.GET.get("g", "")
    esp = request.GET.get("esp", "")
    bi = int(b) if b.isdigit() and int(b) < len(labels) else None
    if bi is not None:
        qs = qs.filter(services.bucket_q(edges, bi))
    if g in dict(GROUPS):
        qs = qs.filter(group=g)
    else:
        g = ""
    qs = _esp_filter(qs, esp)
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(meter_no__icontains=q) | Q(account__icontains=q) | Q(consumer_name__icontains=q) | Q(esp__icontains=q)
                       | Q(substation__icontains=q) | Q(feeder__icontains=q) | Q(address__icontains=q))
    qs = qs.order_by("-days", "esp", "substation", "feeder", "transformer", "account")
    gname = services.GRID_GROUP_NAMES.get(g, "")
    if request.GET.get("pdf"):
        if not request.user.has_perm("reports.export_excel"):
            raise PermissionDenied
        name = exports.list_filename("aloqaga_chiqmay_qolgan_hisoblagichlar", snap, esp, labels[bi] if bi is not None else "", gname).replace(".xlsx", ".pdf")
        desc = " · ".join(x for x in (esp, labels[bi] if bi is not None else "", gname) if x) or "Hammasi"
        data, _cut = pdfs.meter_list_pdf(qs, snap, desc)
        return _pdf_response(request, data, name, f"{snap.report_date:%d.%m.%Y} · {name}")
    if request.GET.get("xlsx"):
        if not request.user.has_perm("reports.export_excel"):
            raise PermissionDenied
        name = exports.list_filename("aloqaga_chiqmay_qolgan_hisoblagichlar", snap, esp, labels[bi] if bi is not None else "", gname)
        return _xlsx_response(request, exports.filtered_meters_xlsx(qs), name, f"{snap.report_date:%d.%m.%Y} · {name}")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    esps = sorted(set(MeterRecord.objects.filter(snapshot=snap).values_list("esp", flat=True)), key=services.esp_sort_key)
    desc = " · ".join(x for x in (esp, labels[bi] if bi is not None else "", gname) if x) or "Hammasi"
    return render(request, "monitoring/meter_list.html", {"snap": snap, "page": page, "labels": labels, "b": b, "g": g, "q": q, "esp": esp,
                                                          "esps": [e or "—" for e in esps], "groups": GROUPS, "m_min": edges[0], "desc": desc})


# ------------------------------------------------------------------ solishtirish
def _nearest_before(snaps, day):
    return next((x for x in snaps if x.report_date <= day), None)


@login_required
@view_perm
def compare(request):
    snaps = list(Snapshot.objects.order_by("-report_date"))
    ctx = {"snaps": snaps, "cmp": None}
    if len(snaps) < 2:
        return render(request, "monitoring/compare.html", ctx)
    by_pk = {str(x.pk): x for x in snaps}
    b = by_pk.get(request.GET.get("b", "")) or snaps[0]
    a = by_pk.get(request.GET.get("a", ""))
    if a is None:
        a = next((x for x in snaps if x.report_date < b.report_date), None)
    if a is None or a.pk == b.pk:
        messages.warning(request, "Solishtirish uchun ikki xil sana tanlang.")
        ctx.update(a=a, b=b)
        return render(request, "monitoring/compare.html", ctx)
    if a.report_date > b.report_date:
        a, b = b, a
    cmp = services.compare_grids(a, b)
    tbl = services.compare_table(cmp, a, b)
    if request.GET.get("xlsx") or request.GET.get("pdf"):
        if not request.user.has_perm("reports.export_excel"):
            raise PermissionDenied
        base = f"solishtirish_{a.report_date:%Y-%m-%d}_{b.report_date:%Y-%m-%d}"
        detail = f"Solishtirish {a.report_date:%d.%m.%Y} → {b.report_date:%d.%m.%Y}"
        if request.GET.get("pdf"):
            return _pdf_response(request, pdfs.compare_pdf(a, b, tbl), base + ".pdf", detail)
        return _xlsx_response(request, exports.compare_xlsx(a, b, tbl), base + ".xlsx", detail)
    quick = []
    older = [x for x in snaps if x.report_date < b.report_date]
    if older:
        quick.append({"label": "Oldingi hisobot bilan", "a": older[0].pk})
    for days, label in ((7, "7 kun oldin bilan"), (30, "30 kun oldin bilan")):
        x = _nearest_before(older, b.report_date - timedelta(days=days))
        if x and all(q["a"] != x.pk for q in quick):
            quick.append({"label": label, "a": x.pk})
    new_dcu = recovered_dcu = None
    if cmp["ok"]["dcu"]:
        a_ids = set(a.dcus.values_list("device_no", flat=True))
        b_ids = set(b.dcus.values_list("device_no", flat=True))
        min_days = (b.summary or {}).get("dcu_edges", [2])[0]
        new_all = b.dcus.exclude(device_no__in=a_ids).order_by("-days", "esp", "device_no")
        rec_all = a.dcus.exclude(device_no__in=b_ids).order_by("-days", "esp", "device_no")
        new_dcu = {"n": new_all.count(), "rows": list(new_all[:300])}
        recovered_dcu = {"n": rec_all.count(), "rows": list(rec_all[:300])}
        ctx["min_days"] = min_days
    ctx.update(a=a, b=b, cmp=cmp, tbl=tbl, g=cmp["grid"], quick=quick, new_dcu=new_dcu, recovered_dcu=recovered_dcu,
               edges_differ=not (cmp["ok"]["dcu"] and cmp["ok"]["m"]))
    return render(request, "monitoring/compare.html", ctx)


@login_required
@export_perm
def download(request, pk, kind):
    snap = get_object_or_404(Snapshot, pk=pk)
    if kind == "svod" and request.GET.get("pdf"):
        name = f"svod_{snap.report_date:%Y-%m-%d}.pdf"
        return _pdf_response(request, pdfs.svod_pdf(snap), name, f"{snap.report_date:%d.%m.%Y} · {name}")
    try:
        data, name = exports.build_file(snap, kind)
    except (KeyError, ValueError):
        raise Http404("Fayl turi noto‘g‘ri") from None
    if kind.startswith("m") and (not snap.has_meters or snap.meter_detail_purged):
        raise Http404("Bu sana uchun hisoblagich ro‘yxati mavjud emas")
    if kind == "dcu" and not snap.has_dcu:
        raise Http404("Konsentratorlar yuklanmagan")
    audit(request.user, "mon_export", f"{snap.report_date:%d.%m.%Y} · {name}")
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


# ------------------------------------------------------------------ svod rasmi
@login_required
@view_perm
def svod_image(request, pk):
    snap = get_object_or_404(Snapshot, pk=pk)
    resp = HttpResponse(image.svod_png(snap), content_type="image/png")
    if request.GET.get("dl"):
        resp["Content-Disposition"] = f'attachment; filename="svod_{snap.report_date:%Y-%m-%d}.png"'
    return resp


# ------------------------------------------------------------------ Telegramga yuborish
@login_required
@send_perm
def send(request, pk):
    snap = get_object_or_404(Snapshot, pk=pk)
    files = exports.available_files(snap)
    cfg = services.get_settings()
    chat_id = (cfg.telegram_chat_id or getattr(dj_settings, "TELEGRAM_CHAT_ID", "")).strip()
    back = lambda: redirect("mon_send", pk=snap.pk)  # noqa: E731
    running = sender.running_job(snap)
    if request.method == "POST":
        kinds = [k for k in request.POST.getlist("kind") if k in {f["kind"] for f in files}]
        if not request.POST.get("confirm"):
            return jobs.reject(request, "Yuborishni tasdiqlang.", back)
        if not telegram.is_configured():
            return jobs.reject(request, telegram.NO_TOKEN_MSG, back)
        if not telegram.valid_chat_id(chat_id):
            return jobs.reject(request, "Telegram guruh ID si sozlanmagan yoki noto‘g‘ri (Sozlamalar sahifasi).", back)
        if running:
            return jobs.reject(request, "Bu hisobot hozir yuborilmoqda — tugashini kuting.", back)
        row = sender.create_log(snap, request.user, kinds, chat_id)
        job = jobs.create("mon_send", request.user, sender.steps_for(snap, kinds), key=sender.job_key(snap),
                          redirect_to=reverse("mon_send", args=[snap.pk]))
        audit(request.user, "mon_send_start", f"{snap.report_date:%d.%m.%Y} · {len(kinds)} fayl")
        return jobs.launch(request, job, sender.send_job, row.pk, use_lock=False, fail_url=reverse("mon_send", args=[snap.pk]))
    ctx = {"snap": snap, "files": files, "chat_id": chat_id, "configured": telegram.is_configured(), "logs": snap.send_logs.all()[:10],
           "chat_title": telegram.chat_title(chat_id), "running": bool(running), "attach_url": jobs.status_url(running["id"]) if running else ""}
    return render(request, "monitoring/send.html", ctx)


@login_required
@manage_perm
@require_POST
def delete(request, pk):
    snap = get_object_or_404(Snapshot, pk=pk)
    label = f"{snap.report_date:%d.%m.%Y}"
    services.delete_snapshot(snap)
    audit(request.user, "mon_delete", label)
    messages.success(request, f"{label} hisoboti o‘chirildi.")
    return redirect("mon_archive")


# ------------------------------------------------------------------ sozlamalar
@login_required
@manage_perm
def settings_view(request):
    cfg = services.get_settings()
    if request.method == "POST":
        errors = []
        try:
            services.parse_edges(request.POST.get("dcu_edges", ""), "Konsentrator chegaralari")
        except ValueError as e:
            errors.append(str(e))
        try:
            services.parse_edges(request.POST.get("meter_edges", ""), "Hisoblagich chegaralari")
        except ValueError as e:
            errors.append(str(e))
        chat = request.POST.get("telegram_chat_id", "").strip()
        if chat and not telegram.valid_chat_id(chat):
            errors.append("Telegram guruh ID si noto‘g‘ri (masalan -1001234567890 yoki @guruh_nomi).")
        try:
            keep = int(request.POST.get("detail_keep_days", "14"))
            if not 1 <= keep <= 365:
                raise ValueError
        except ValueError:
            errors.append("Saqlash muddati 1 dan 365 gacha bo‘lsin.")
            keep = cfg.detail_keep_days
        if services.split_words(request.POST.get("offline_keywords", "")) == []:
            errors.append("Offline holat so‘zlari bo‘sh bo‘lmasin.")
        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            cfg.dcu_edges = ", ".join(map(str, services.parse_edges(request.POST["dcu_edges"])))
            cfg.meter_edges = ", ".join(map(str, services.parse_edges(request.POST["meter_edges"])))
            for f in ("kw_yuridik", "kw_aholi", "kw_balans", "offline_keywords"):
                setattr(cfg, f, ", ".join(services.split_words(request.POST.get(f, ""))))
            cfg.use_file_time = bool(request.POST.get("use_file_time"))
            cfg.telegram_chat_id, cfg.detail_keep_days = chat, keep
            cfg.save()
            services.recompute_groups(cfg)
            audit(request.user, "mon_settings", f"dcu={cfg.dcu_edges}; meter={cfg.meter_edges}; chat={'bor' if chat else 'yo‘q'}")
            messages.success(request, "Sozlamalar saqlandi, svodlar qayta hisoblandi.")
            return redirect("mon_settings")
    return render(request, "monitoring/settings.html", {
        "cfg": cfg, "configured": telegram.is_configured(), "chat_title": telegram.chat_title((cfg.telegram_chat_id or getattr(dj_settings, "TELEGRAM_CHAT_ID", ""))), "env_chat": getattr(dj_settings, "TELEGRAM_CHAT_ID", ""),
        "token_source": telegram.token_source(),
    })


@login_required
@manage_perm
@require_POST
def settings_test(request):
    cfg = services.get_settings()
    chat_id = (cfg.telegram_chat_id or getattr(dj_settings, "TELEGRAM_CHAT_ID", "")).strip()
    if not telegram.is_configured():
        messages.error(request, telegram.NO_TOKEN_MSG)
    elif not telegram.valid_chat_id(chat_id):
        messages.error(request, "Avval Telegram guruh ID sini saqlang.")
    else:
        try:
            telegram.send_message(chat_id, "✅ Offline monitoring: sinov xabari. Bot guruhga ulangan.")
            messages.success(request, "Sinov xabari yuborildi.")
        except telegram.TelegramError as exc:
            messages.error(request, str(exc))
    return redirect("mon_settings")
