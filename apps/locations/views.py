from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from apps.core import jobs
from apps.customers.models import BotUser
from apps.reports.services import audit

from . import rules, services
from .models import LocationSearch, LocationUpload, Place

view_perm = permission_required("reports.view_location", raise_exception=True)
upload_perm = permission_required("reports.upload_location", raise_exception=True)
export_perm = permission_required("reports.export_location", raise_exception=True)
manage_perm = permission_required("reports.manage_location", raise_exception=True)
log_perm = permission_required("reports.view_location_log", raise_exception=True)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _day(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _place_qs(params):
    qs = Place.objects.all()
    if params.get("kind") in rules.KINDS.values():
        qs = qs.filter(kind=params["kind"])
    if params.get("district") in rules.DISTRICTS:
        qs = qs.filter(district=params["district"])
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(account__icontains=q) | Q(fio__icontains=q) | Q(address__icontains=q)
                       | Q(substation__icontains=q) | Q(feeder__icontains=q) | Q(tp__icontains=q))
    return qs


def _filters(p):
    return {k: p.get(k, "") for k in ("kind", "district", "q")}


def _qs(p, keys):
    return "&".join(f"{k}={p.get(k)}" for k in keys if p.get(k))


# ------------------------------------------------------------------ ma'lumotlar
@login_required
@view_perm
def data(request):
    p = request.GET
    qs = _place_qs(p).order_by("number")
    counts = {r["kind"]: r["n"] for r in Place.objects.values("kind").annotate(n=Count("id")).order_by()}
    page = Paginator(qs, 50).get_page(p.get("page"))
    return render(request, "locations/data.html", {
        "page": page, "count": page.paginator.count, "f": _filters(p), "qs": _qs(p, ("kind", "district", "q")),
        "kinds": rules.KINDS.values(), "districts": rules.DISTRICTS, "counts": counts, "total": sum(counts.values()),
        "can_export": request.user.has_perm("reports.export_location"),
    })


@login_required
@view_perm
def place_detail(request, pk):
    pl = get_object_or_404(Place, pk=pk)
    return render(request, "locations/detail.html", {"p": pl, "can_manage": request.user.has_perm("reports.manage_location"),
                                                     "maps": services.maps_buttons(pl)["inline_keyboard"]})


@login_required
@manage_perm
@require_POST
def place_delete(request, pk):
    pl = get_object_or_404(Place, pk=pk)
    audit(request.user, "location_delete", f"{pl.kind} {pl.number}")
    pl.delete()
    messages.success(request, f"{pl.number} o‘chirildi.")
    return redirect("location_data")


# ------------------------------------------------------------------ xarita
@login_required
@view_perm
def map_page(request):
    p = request.GET
    return render(request, "locations/map.html", {"f": _filters(p), "kinds": rules.KINDS.values(), "districts": rules.DISTRICTS})


@login_required
@view_perm
def map_points(request):
    """Xarita uchun yengil nuqtalar: [id, lat, lon, tur_indeksi, nomer]."""
    kinds = list(rules.KINDS.values())
    rows = _place_qs(request.GET).values_list("id", "lat", "lon", "kind", "number")[:30000]
    resp = JsonResponse({"points": [[i, round(la, 6), round(lo, 6), kinds.index(k), n] for i, la, lo, k, n in rows], "kinds": kinds})
    resp["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ yuklash
@login_required
@upload_perm
def upload(request):
    if request.method == "POST":
        f = request.FILES.get("file")
        back = lambda: redirect("location_upload")  # noqa: E731
        if f is None:
            return jobs.reject(request, "Excel faylni tanlang.", back)
        if not f.name.lower().endswith(".xlsx"):
            return jobs.reject(request, "Faqat Excel (.xlsx) fayl qabul qilinadi.", back)
        if f.size > rules.MAX_BYTES:
            return jobs.reject(request, "Fayl hajmi 60 MB dan oshmasligi kerak.", back)
        if jobs.active(f"location:{request.user.pk}"):
            return jobs.reject(request, "Oldingi yuklash hali tugamadi. Tugashini kuting.", back)
        services.cleanup_tmp()
        path = services.base_dir() / "tmp" / f"{uuid.uuid4().hex}.xlsx"
        with open(path, "wb") as out:
            for chunk in f.chunks():
                out.write(chunk)
        job = jobs.create("location_upload", request.user, services.STEPS, key=f"location:{request.user.pk}")
        return jobs.launch(request, job, services.run_upload, request.user.pk, str(path), f.name, fail_url="/location/yuklash/")
    active = jobs.active(f"location:{request.user.pk}")
    return render(request, "locations/upload.html", {
        "attach": jobs.status_url(active["id"]) if active else "", "total": Place.objects.count(),
        "recent": LocationUpload.objects.all()[:5],
    })


@login_required
@view_perm
def uploads(request):
    page = Paginator(LocationUpload.objects.all(), 30).get_page(request.GET.get("page"))
    new = request.GET.get("new", "")
    return render(request, "locations/uploads.html", {"page": page, "new": int(new) if new.isdigit() else 0,
                                                      "can_upload": request.user.has_perm("reports.upload_location")})


@login_required
@view_perm
def upload_errors(request, pk):
    up = get_object_or_404(LocationUpload, pk=pk)
    path = services.base_dir() / up.error_file if up.error_file else None
    if not path or not path.exists():
        raise Http404
    return FileResponse(open(path, "rb"), as_attachment=True, filename=f"xatolar_{timezone.localtime(up.created_at):%d.%m.%Y_%H%M}.xlsx", content_type=XLSX)


@login_required
@view_perm
def template_file(request):
    resp = HttpResponse(rules.build_template(), content_type=XLSX)
    resp["Content-Disposition"] = 'attachment; filename="LOCATION_SHABLON.xlsx"'
    return resp


@login_required
@export_perm
def export(request):
    qs = _place_qs(request.GET).order_by("number")
    audit(request.user, "location_export", f"{qs.count()} ta yozuv")
    resp = HttpResponse(rules.build_export(qs), content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="location_{timezone.localtime():%Y-%m-%d_%H%M}.xlsx"'
    return resp


# ------------------------------------------------------------------ qidiruv jurnali
def _search_qs(p):
    qs = LocationSearch.objects.select_related("bot_user", "place")
    if str(p.get("user", "")).isdigit():
        qs = qs.filter(bot_user_id=int(p["user"]))
    if p.get("found") in ("1", "0"):
        qs = qs.filter(found=p["found"] == "1")
    q = (p.get("q") or "").strip()
    if q:
        qs = qs.filter(query__icontains=q)
    tz = timezone.get_current_timezone()
    d1, d2 = _day(p.get("d1")), _day(p.get("d2"))
    if d1:
        qs = qs.filter(created_at__gte=datetime.combine(d1, time.min, tz))
    if d2:
        qs = qs.filter(created_at__lt=datetime.combine(d2 + timedelta(days=1), time.min, tz))
    return qs


@login_required
@log_perm
def searches(request):
    p = request.GET
    qs = _search_qs(p)
    tot = qs.aggregate(n=Count("id"), ok=Count("id", filter=Q(found=True)))
    page = Paginator(qs, 50).get_page(p.get("page"))
    return render(request, "locations/searches.html", {
        "page": page, "n": tot["n"], "ok": tot["ok"], "miss": tot["n"] - tot["ok"],
        "f": {k: p.get(k, "") for k in ("user", "found", "q", "d1", "d2")}, "qs": _qs(p, ("user", "found", "q", "d1", "d2")),
        "users": BotUser.objects.filter(location_searches__isnull=False).distinct().order_by("full_name"),
    })


@login_required
@log_perm
def searches_export(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Qidiruv jurnali"
    ws.append(["Vaqt", "Foydalanuvchi", "Tuman (foyd.)", "Telefon", "Qidirilgan raqam", "Natija", "Turi", "Tuman", "FIO"])
    for s in _search_qs(request.GET).order_by("-created_at")[:50000]:
        u, pl = s.bot_user, s.place
        ws.append([timezone.localtime(s.created_at).strftime("%d.%m.%Y %H:%M:%S"), getattr(u, "full_name", "") or "", getattr(u, "district", "") or "",
                   getattr(u, "phone", "") or "", s.query, "Topildi" if s.found else "Topilmadi", getattr(pl, "kind", ""),
                   getattr(pl, "district", ""), getattr(pl, "fio", "")])
    for col, w in zip("ABCDEFGHI", (19, 30, 20, 16, 20, 11, 9, 18, 28)):
        ws.column_dimensions[col].width = w
    b = BytesIO()
    wb.save(b)
    audit(request.user, "location_log_export", "")
    resp = HttpResponse(b.getvalue(), content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="qidiruv_jurnali_{timezone.localtime():%Y-%m-%d_%H%M}.xlsx"'
    return resp
