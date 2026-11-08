from __future__ import annotations

from datetime import datetime
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook

from apps.core import jobs
from apps.customers.models import BotUser
from apps.reports.services import audit

from . import services
from .models import Post, Recipient

view_perm = permission_required("reports.view_posts", raise_exception=True)
send_perm = permission_required("reports.send_posts", raise_exception=True)
manage_perm = permission_required("reports.manage_posts", raise_exception=True)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _stats(post):
    agg = post.recipients.aggregate(
        total=Count("id"), sent=Count("id", filter=Q(status="sent")), failed=Count("id", filter=Q(status="failed")),
        pending=Count("id", filter=Q(status="pending")), confirmed=Count("id", filter=Q(confirmed_at__isnull=False)))
    return agg


@login_required
@view_perm
def posts(request):
    page = Paginator(Post.objects.all(), 30).get_page(request.GET.get("page"))
    for p in page:
        p.st = _stats(p)
    return render(request, "broadcasts/list.html", {"page": page, "can_send": request.user.has_perm("reports.send_posts")})


@login_required
@send_perm
def post_new(request):
    users = BotUser.objects.filter(status="approved").order_by("district", "full_name")
    districts = sorted({u.district for u in users if u.district})
    if request.method == "POST":
        p = request.POST
        text, upload = (p.get("text") or "").strip(), request.FILES.get("file")
        mode = p.get("mode") if p.get("mode") in ("all", "district", "selected") else "all"
        back = lambda: redirect("post_new")  # noqa: E731
        err = services.validate(text, upload)
        if err:
            return jobs.reject(request, err, back)
        picked_d = [d for d in p.getlist("districts") if d in districts]
        picked_u = [int(x) for x in p.getlist("users") if x.isdigit()]
        targets = list(services.audience_users(mode, picked_d, picked_u))
        if not targets:
            return jobs.reject(request, "Qabul qiluvchi tanlanmadi (yoki tanlanganlar orasida tasdiqlangan foydalanuvchi yo‘q).", back)
        deadline = None
        if p.get("confirm") and p.get("deadline"):
            try:
                deadline = timezone.make_aware(datetime.strptime(p["deadline"], "%Y-%m-%dT%H:%M"))
            except ValueError:
                return jobs.reject(request, "Muddat formati noto‘g‘ri.", back)
        label = {"all": "Hamma foydalanuvchilar", "district": "Tuman: " + ", ".join(picked_d), "selected": f"Tanlangan: {len(targets)} ta"}[mode]
        post = Post(kind=services.detect_kind(upload) if upload else "text", text=text, confirm_required=bool(p.get("confirm")),
                    deadline=deadline, audience=label[:200], created_by=request.user)
        if upload:
            post.file_name = upload.name[:200]
        post.save()
        if upload:
            post.file.save(upload.name, upload, save=True)
        Recipient.objects.bulk_create([Recipient(post=post, user=u) for u in targets])
        audit(request.user, "post_create", f"#{post.pk} · {post.get_kind_display()} · {len(targets)} ta qabul qiluvchi")
        job = jobs.create("post_send", request.user, services.SEND_STEPS, key=f"post:{post.pk}")
        return jobs.launch(request, job, services.run_send, post.pk, use_lock=False, fail_url=f"/postlar/{post.pk}/")
    return render(request, "broadcasts/new.html", {"users": users, "districts": districts, "total": users.count()})


@login_required
@view_perm
def post_detail(request, pk):
    post = get_object_or_404(Post, pk=pk)
    active = jobs.active(f"post:{post.pk}")
    recs = post.recipients.select_related("user")
    flt = request.GET.get("f", "")
    if flt == "wait":
        recs = recs.filter(status="sent", confirmed_at__isnull=True)
    elif flt == "done":
        recs = recs.filter(confirmed_at__isnull=False)
    elif flt == "fail":
        recs = recs.filter(status="failed")
    return render(request, "broadcasts/detail.html", {
        "post": post, "st": _stats(post), "recs": recs, "flt": flt, "attach": jobs.status_url(active["id"]) if active else "",
        "can_manage": request.user.has_perm("reports.manage_posts"),
        "overdue": bool(post.deadline and post.deadline < timezone.now()),
    })


def _job_action(request, pk, kind, fn, steps, key_prefix, ok_msg_busy):
    post = get_object_or_404(Post, pk=pk)
    if jobs.active(f"post:{post.pk}"):
        return jobs.reject(request, ok_msg_busy, lambda: redirect("post_detail", pk=pk))
    job = jobs.create(kind, request.user, steps, key=f"post:{post.pk}")
    return jobs.launch(request, job, fn, post.pk, use_lock=False, fail_url=f"/postlar/{post.pk}/")


@login_required
@manage_perm
@require_POST
def post_remind(request, pk):
    audit(request.user, "post_remind", f"#{pk}")
    return _job_action(request, pk, "post_remind", services.run_remind,
                       [("Tanishmaganlar aniqlanmoqda", 1), ("Eslatma yuborilmoqda", 8), ("Tayyor", 1)], "post", "Bu post bilan boshqa jarayon ketmoqda.")


@login_required
@manage_perm
@require_POST
def post_delete(request, pk):
    audit(request.user, "post_delete", f"#{pk}")
    return _job_action(request, pk, "post_delete", services.run_delete,
                       [("Xabarlar aniqlanmoqda", 1), ("Foydalanuvchilardan o‘chirilmoqda", 8), ("Tayyor", 1)], "post", "Bu post bilan boshqa jarayon ketmoqda.")


@login_required
@view_perm
def post_export(request, pk):
    post = get_object_or_404(Post, pk=pk)
    wb = Workbook()
    ws = wb.active
    ws.title = "Hisobot"
    ws.append(["FIO", "Tuman / shahar", "Telefon", "Lavozim", "Yuborilish", "Yuborilgan vaqt", "Tanishdi", "Tanishgan vaqt", "Izoh"])
    for r in post.recipients.select_related("user"):
        u = r.user
        ws.append([u.full_name, u.district, u.phone, u.position, dict(Recipient.STATUS)[r.status],
                   timezone.localtime(r.sent_at).strftime("%d.%m.%Y %H:%M") if r.sent_at else "",
                   "Ha" if r.confirmed_at else ("Yo‘q" if post.confirm_required else "—"),
                   timezone.localtime(r.confirmed_at).strftime("%d.%m.%Y %H:%M") if r.confirmed_at else "", r.error])
    for col, w in zip("ABCDEFGHI", (34, 22, 16, 22, 12, 17, 10, 17, 36)):
        ws.column_dimensions[col].width = w
    b = BytesIO()
    wb.save(b)
    resp = HttpResponse(b.getvalue(), content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="post_{post.pk}_hisobot.xlsx"'
    return resp
