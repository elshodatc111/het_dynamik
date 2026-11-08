from __future__ import annotations

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import jobs

from . import services, tasks
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
def dashboard(request):
    """Bosh sahifa — hozircha bo‘sh (loyiha yakunlangach tayyorlanadi)."""
    return render(request, "home.html")


@login_required
@view_perm
def dynamics(request):
    """Dinamika bo‘limining umumiy ko‘rinishi (oldingi bosh sahifa)."""
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
    if request.GET.get("fmt") == "pdf":
        from .pdfs import compare_pdf

        services.audit(request.user, "export", f"PDF: {a:%d.%m.%Y} → {b:%d.%m.%Y}")
        resp = HttpResponse(compare_pdf(comp, request.user), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="dinamika_{a:%Y-%m-%d}_{b:%Y-%m-%d}.pdf"'
        return resp
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
    """PDF larni qabul qilib saqlaydi va tekshirishni orqa fonda boshlaydi (jonli progress bilan)."""
    if request.method != "POST":
        return render(request, "upload.html", {})
    back = lambda: redirect("upload")  # noqa: E731
    files = request.FILES.getlist("files")
    if not files:
        return jobs.reject(request, "PDF fayl tanlang.", back)

    from django.conf import settings

    items, precards = [], []
    for f in files:
        err = None
        if not f.name.lower().endswith(".pdf"):
            err = "Faqat PDF fayl qabul qilinadi."
        elif f.size > settings.MAX_PDF_SIZE:
            err = "Fayl juda katta."
        else:
            head = f.read(5)
            f.seek(0)
            if head != b"%PDF-":
                err = "Bu haqiqiy PDF fayl emas."
        if err:
            precards.append({"token": None, "name": f.name, "ok": False, "errors": [err], "warnings": [], "report_date": None,
                             "prev_date": None, "pages": 0, "sections": 0, "skipped_pages": [], "summary": []})
        else:
            token, path = services.stash_upload(f)
            items.append((token, str(path), f.name))
    job = jobs.create("pdf_analyse", request.user, tasks.analyse_steps([n for _, _, n in items]))
    job.d["redirect"] = reverse("upload_result", args=[job.id])
    return jobs.launch(request, job, tasks.analyse_job, items, precards, fail_url=reverse("upload"))


@login_required
@upload_perm
def upload_result(request, job_id):
    """Tekshiruv natijasi (job tugagach)."""
    data = jobs.load(job_id, request.user)
    if not data or data["status"] != "done" or data.get("kind") != "pdf_analyse":
        messages.error(request, "Natija topilmadi yoki muddati o‘tgan. Fayllarni qaytadan yuklang.")
        return redirect("upload")
    cards, pending = [], {}
    for c in data["result"]["cards"]:
        rd = services.parse_iso(c["report_date"])
        rep = {"ok": c["ok"], "report_date": rd, "prev_date": services.parse_iso(c["prev_date"]), "pages": c["pages"],
               "sections": range(c["sections"]), "skipped_pages": c["skipped_pages"]} if c["token"] or c["ok"] else None
        from decimal import Decimal

        c["summary"] = [dict(s, first=[(lb, Decimal(str(v)), kind) for lb, v, kind in s["first"]]) for s in c["summary"]]
        card = dict(c, report=rep, duplicate=None)
        if c["ok"] and rd:
            card["duplicate"] = Upload.objects.filter(report_date=rd).select_related("uploaded_by").first()
            pending[c["token"]] = c["name"]
        cards.append(card)
    cards.sort(key=lambda c: c["report"]["report_date"] if c["report"] and c["ok"] else date.max)
    request.session["pending_uploads"] = pending
    return render(request, "upload_preview.html", {
        "cards": cards, "can_replace": request.user.has_perm("reports.manage_uploads"),
        "has_ok": any(c["ok"] for c in cards),
    })


@login_required
@upload_perm
@require_POST
def upload_confirm(request):
    back = lambda: redirect("upload")  # noqa: E731
    pending = request.session.get("pending_uploads", {})
    can_replace = request.user.has_perm("reports.manage_uploads")
    replace_tokens = set(request.POST.getlist("replace")) if can_replace else set()
    wanted = set(request.POST.getlist("token")) | replace_tokens
    tokens = [t for t in wanted if t in pending]
    if not tokens:
        return jobs.reject(request, "Saqlash uchun fayl tanlanmadi.", back)
    entries = [(t, pending[t], t in replace_tokens) for t in tokens]
    job = jobs.create("pdf_save", request.user, tasks.save_steps([e[1] for e in entries]))
    job.d["redirect"] = reverse("upload_done", args=[job.id])
    request.session["pending_uploads"] = {}
    return jobs.launch(request, job, tasks.save_job, entries, request.user.pk, fail_url=reverse("upload"))


@login_required
@upload_perm
def upload_done(request, job_id):
    data = jobs.load(job_id, request.user)
    if data and data["status"] == "done" and data.get("kind") == "pdf_save":
        res = data["result"]
        fmt = lambda xs: ", ".join(f"{services.parse_iso(x):%d.%m.%Y}" for x in xs)  # noqa: E731
        if res.get("saved"):
            messages.success(request, "Saqlandi: " + fmt(res["saved"]))
        if res.get("skipped"):
            messages.warning(request, "O‘tkazib yuborildi (bu sana allaqachon bor): " + fmt(res["skipped"]))
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
