from __future__ import annotations

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .excel import build_workbook
from .models import AuditLog, District, Metric, Section, Upload
from .parser.districts import TOTAL_KEY

view_perm = permission_required("reports.view_dashboard", raise_exception=True)
export_perm = permission_required("reports.export_excel", raise_exception=True)
upload_perm = permission_required("reports.upload_pdf", raise_exception=True)
manage_perm = permission_required("reports.manage_uploads", raise_exception=True)
audit_perm = permission_required("reports.view_audit", raise_exception=True)


# ------------------------------------------------------------------ dashboard
@login_required
@view_perm
def dashboard(request):
    return render(request, "dashboard.html", {"data": services.dashboard_data()})


# ------------------------------------------------------------------ solishtirish
def _pick_dates(request):
    dates = [d for d, _ in services.available_dates()]
    a = services.parse_iso(request.GET.get("a"))
    b = services.parse_iso(request.GET.get("b"))
    if not b:
        b = dates[0] if dates else None
    if not a and b:
        a = next((d for d in dates if d < b), None)
    return dates, a, b


@login_required
@view_perm
def compare(request):
    dates, a, b = _pick_dates(request)
    ctx = {"all_dates": services.available_dates(), "a": a, "b": b, "comp": None,
           "quick": services.quick_ranges(dates)}
    if a and b:
        if a == b:
            messages.warning(request, "Ikkala sana bir xil — turli sanalarni tanlang.")
        else:
            if a > b:
                a, b = b, a
            ctx.update(a=a, b=b, comp=services.build_comparison(a, b))
    return render(request, "compare.html", ctx)


@login_required
@export_perm
def compare_export(request):
    a, b = services.parse_iso(request.GET.get("a")), services.parse_iso(request.GET.get("b"))
    if not a or not b or a == b:
        raise Http404("Sanalar noto‘g‘ri")
    if a > b:
        a, b = b, a
    comp = services.build_comparison(a, b)
    if not comp["sections"]:
        raise Http404("Tanlangan sanalar uchun ma’lumot yo‘q")
    content = build_workbook(comp, request.user)
    services.audit(request.user, "export", f"Excel: {a:%d.%m.%Y} → {b:%d.%m.%Y}")
    resp = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="dinamika_{a:%Y-%m-%d}_{b:%Y-%m-%d}.xlsx"'
    return resp


# ------------------------------------------------------------------ trend
@login_required
@view_perm
def trend(request):
    sections = Section.objects.prefetch_related("metrics")
    meta = [
        {"key": s.key, "title": s.short,
         "metrics": [{"key": m.key, "label": (f"{m.group}: {m.label}" if m.group else m.label)} for m in s.metrics.all()]}
        for s in sections
    ]
    districts = [{"key": d.key, "name": d.name} for d in District.objects.all()]
    dates = [d for d, _ in services.available_dates()]
    return render(request, "trend.html", {
        "meta_json": meta,
        "districts_json": districts,
        "date_min": dates[-1].isoformat() if dates else "",
        "date_max": dates[0].isoformat() if dates else "",
    })


@login_required
@view_perm
def trend_data(request):
    today = timezone.localdate()
    d_to = services.parse_iso(request.GET.get("to")) or today
    d_from = services.parse_iso(request.GET.get("from")) or (d_to - timedelta(days=30))
    keys = [k for k in request.GET.getlist("d") if k][:8] or [TOTAL_KEY]
    data = services.trend_data(request.GET.get("section", ""), request.GET.get("metric", ""), keys, d_from, d_to)
    return JsonResponse(data)


# ------------------------------------------------------------------ yuklash
@login_required
@upload_perm
def upload(request):
    if request.method != "POST":
        return render(request, "upload.html", {})

    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "PDF fayl tanlang.")
        return render(request, "upload.html", {})

    from django.conf import settings

    cards, pending = [], {}
    for f in files:
        card = {"name": f.name, "token": None, "report": None, "errors": [], "warnings": [], "duplicate": None}
        if not f.name.lower().endswith(".pdf"):
            card["errors"].append("Faqat PDF fayl qabul qilinadi.")
        elif f.size > settings.MAX_PDF_SIZE:
            card["errors"].append("Fayl juda katta.")
        else:
            head = f.read(5)
            f.seek(0)
            if head != b"%PDF-":
                card["errors"].append("Bu haqiqiy PDF fayl emas.")
        if not card["errors"]:
            token, path = services.stash_upload(f)
            rep = services.analyse_file(path)
            card.update(token=token, report=rep, errors=list(rep.errors), warnings=list(rep.warnings))
            if rep.ok:
                card["duplicate"] = Upload.objects.filter(report_date=rep.report_date).first()
                card["summary"] = _summary(rep)
                pending[token] = f.name
        cards.append(card)

    request.session["pending_uploads"] = pending
    cards.sort(key=lambda c: c["report"].report_date if c["report"] and c["report"].ok else date.max)
    ok_cards = [c for c in cards if c["report"] and c["report"].ok]
    return render(request, "upload_preview.html", {
        "cards": cards, "can_replace": request.user.has_perm("reports.manage_uploads"),
        "has_ok": bool(ok_cards),
    })


def _summary(rep):
    """Ko‘rib chiqish oynasi uchun qisqa jadval: har bir bo‘limdagi «Jami» qiymatlardan bir nechtasi."""
    out = []
    for key, sd in rep.sections.items():
        total = sd.cur.get(TOTAL_KEY, {})
        from .parser.catalog import SECTION_BY_KEY

        sdef = SECTION_BY_KEY[key]
        first = [(m.label, total[m.key], m.kind) for m in sdef.metrics if m.key in total][:3]
        out.append({"title": sdef.short, "districts": len(sd.cur) - 1, "page": sd.page, "first": first})
    return out


@login_required
@upload_perm
@require_POST
def upload_confirm(request):
    pending = request.session.get("pending_uploads", {})
    can_replace = request.user.has_perm("reports.manage_uploads")
    replace_tokens = set(request.POST.getlist("replace")) if can_replace else set()
    wanted = set(request.POST.getlist("token")) | replace_tokens
    tokens = [t for t in wanted if t in pending]
    if not tokens:
        messages.error(request, "Saqlash uchun fayl tanlanmadi.")
        return redirect("upload")

    items = []
    for t in tokens:
        path = services.pending_path(t)
        if not path:
            continue
        rep = services.analyse_file(path)
        if rep.ok:
            items.append((rep, path, pending[t], t))
    items.sort(key=lambda x: x[0].report_date)  # eski sanalar avval — «oldingi holat» to‘g‘ri to‘ldirilsin

    saved, skipped = [], []
    for rep, path, name, t in items:
        try:
            services.save_report(rep, path, name, request.user, replace=(t in replace_tokens))
            saved.append(rep.report_date)
            path.unlink(missing_ok=True)
            pending.pop(t, None)
        except services.DuplicateUpload as exc:
            skipped.append(f"{exc.existing.report_date:%d.%m.%Y}")
    request.session["pending_uploads"] = pending
    if saved:
        messages.success(request, "Saqlandi: " + ", ".join(f"{d:%d.%m.%Y}" for d in saved))
    if skipped:
        messages.warning(request, "O‘tkazib yuborildi (bu sana allaqachon bor): " + ", ".join(skipped))
    return redirect("archive")


# ------------------------------------------------------------------ arxiv
@login_required
@view_perm
def archive(request):
    uploads = Upload.objects.select_related("uploaded_by")
    return render(request, "archive.html", {"uploads": uploads})


@login_required
@export_perm
def upload_file(request, pk):
    up = get_object_or_404(Upload, pk=pk)
    try:
        fh = up.file.open("rb")
    except FileNotFoundError:
        raise Http404("Fayl topilmadi")
    return FileResponse(fh, as_attachment=True, filename=f"hisobot_{up.report_date:%Y-%m-%d}.pdf",
                        content_type="application/pdf")


@login_required
@manage_perm
@require_POST
def upload_delete(request, pk):
    up = get_object_or_404(Upload, pk=pk)
    label = f"{up.report_date:%d.%m.%Y}"
    services.delete_upload(up, request.user)
    messages.success(request, f"{label} hisoboti o‘chirildi.")
    return redirect("archive")


# ------------------------------------------------------------------ audit
@login_required
@audit_perm
def audit(request):
    qs = AuditLog.objects.all()
    action = request.GET.get("action", "")
    q = request.GET.get("q", "").strip()
    if action:
        qs = qs.filter(action=action)
    if q:
        qs = qs.filter(detail__icontains=q) | qs.filter(username__icontains=q)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    actions = AuditLog.objects.order_by().values_list("action", flat=True).distinct()
    return render(request, "audit.html", {"page": page, "actions": sorted(actions), "action": action, "q": q})
