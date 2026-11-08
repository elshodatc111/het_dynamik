from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.reports.services import audit

from . import bot, rules
from .models import BotUser, Customer, EtkStat, Submission

view_perm = permission_required("reports.view_customers", raise_exception=True)
manage_perm = permission_required("reports.manage_customers", raise_exception=True)
approve_perm = permission_required("reports.approve_customers", raise_exception=True)
stats_perm = permission_required("reports.view_customer_stats", raise_exception=True)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _day(s: str):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _range(qs, params, field="created_at"):
    d1, d2 = _day(params.get("d1")), _day(params.get("d2"))
    tz = timezone.get_current_timezone()
    if d1:
        qs = qs.filter(**{f"{field}__gte": datetime.combine(d1, time.min, tz)})
    if d2:
        qs = qs.filter(**{f"{field}__lt": datetime.combine(d2 + timedelta(days=1), time.min, tz)})
    return qs


def _users():
    return BotUser.objects.exclude(status="new").order_by("full_name")


def _customer_qs(params):
    qs = Customer.objects.select_related("user")
    if params.get("etk") in rules.ETK_CODES:
        qs = qs.filter(etk_code=params["etk"])
    if str(params.get("user", "")).isdigit():
        qs = qs.filter(user_id=int(params["user"]))
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(meter_no__icontains=q) | Q(consumer_name__icontains=q) | Q(consumer_code__icontains=q) | Q(trans_code__icontains=q))
    return _range(qs, params)


# ------------------------------------------------------------------ qabul qilingan ma'lumotlar
@login_required
@view_perm
def data(request):
    qs = _customer_qs(request.GET).order_by("-id")
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    p = request.GET
    return render(request, "customers/data.html", {
        "page": page, "count": page.paginator.count, "etks": rules.ETK_CODES, "users": _users(),
        "f": {k: p.get(k, "") for k in ("etk", "user", "q", "d1", "d2")},
        "qs": "&".join(f"{k}={p.get(k)}" for k in ("etk", "user", "q", "d1", "d2") if p.get(k)),
        "can_manage": request.user.has_perm("reports.manage_customers"),
    })


@login_required
@manage_perm
@require_POST
def export(request):
    """Excelga yuklab olish. delete=1 bo‘lsa, yuklab olingan yozuvlar bazadan o‘chiriladi."""
    qs = _customer_qs(request.POST)
    last = qs.aggregate(m=Max("id"))["m"]
    if not last:
        messages.warning(request, "Yuklab olish uchun ma’lumot yo‘q.")
        return redirect("customer_data")
    qs = qs.filter(id__lte=last).order_by("id")   # shu paytgacha kelganlar; keyin kelganlarga tegilmaydi
    delete = request.POST.get("delete") == "1"
    data = rules.build_export(qs)
    count = qs.count()
    if delete:
        ids = list(qs.values_list("pk", flat=True))
        for i in range(0, len(ids), 2000):
            Customer.objects.filter(pk__in=ids[i:i + 2000]).delete()
    audit(request.user, "customer_export", f"{count} ta yozuv; {'bazadan o‘chirildi' if delete else 'o‘chirilmadi'}")
    name = f"customer_{timezone.localtime():%Y-%m-%d_%H%M}.xlsx"
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    resp["Cache-Control"] = "no-store"
    return resp


# ------------------------------------------------------------------ jurnal
@login_required
@view_perm
def journal(request):
    qs = Submission.objects.select_related("user")
    p = request.GET
    if str(p.get("user", "")).isdigit():
        qs = qs.filter(user_id=int(p["user"]))
    qs = _range(qs, p)
    tot = qs.aggregate(rows=Sum("total"), ok=Sum("ok"), bad=Sum("bad"))
    page = Paginator(qs, 50).get_page(p.get("page"))
    return render(request, "customers/journal.html", {
        "page": page, "users": _users(), "f": {k: p.get(k, "") for k in ("user", "d1", "d2")},
        "qs": "&".join(f"{k}={p.get(k)}" for k in ("user", "d1", "d2") if p.get(k)),
        "sent": page.paginator.count, "rows": tot["rows"] or 0, "ok": tot["ok"] or 0, "bad": tot["bad"] or 0,
    })


# ------------------------------------------------------------------ arizalar (bot foydalanuvchilari)
TABS = (("pending", "Kutilmoqda"), ("approved", "Tasdiqlangan"), ("rejected", "Bekor qilingan"), ("blocked", "Bloklangan"))


@login_required
@approve_perm
def applications(request):
    status = request.GET.get("status", "pending")
    if status not in dict(TABS):
        status = "pending"
    counts = {s: BotUser.objects.filter(status=s).count() for s, _ in TABS}
    users = BotUser.objects.filter(status=status).order_by("-applied_at", "-id")
    sent = {r["user"]: r["n"] for r in Submission.objects.values("user").annotate(n=Sum("ok")).order_by()}
    for u in users:
        u.accepted = sent.get(u.pk, 0)
    return render(request, "customers/applications.html", {
        "tabs": [(s, t, counts[s]) for s, t in TABS], "status": status, "users": users, "bot_alive": _bot_alive(),
    })


def _bot_alive() -> bool:
    import time as _t
    hb = bot.read_heartbeat()
    return bool(hb) and _t.time() - hb.get("ts", 0) < bot.ALIVE_SECONDS


@login_required
@approve_perm
@require_POST
def application_action(request, pk):
    u = get_object_or_404(BotUser, pk=pk)
    action = request.POST.get("action")
    tab = request.POST.get("tab", "pending")
    back = redirect(f"{reverse('customer_apps')}?status={tab if tab in dict(TABS) else 'pending'}")
    if action in ("approve", "reject") and u.status not in ("pending", "rejected"):
        messages.error(request, "Bu ariza hozir ko‘rib chiqilmaydi.")
        return back
    if action == "approve":
        fields = {k: (request.POST.get(k) or "").strip() for k in ("full_name", "district", "phone", "position")}
        if not all(fields.values()):
            messages.error(request, "FIO, tuman/shahar, telefon va lavozim to‘ldirilishi shart.")
            return back
        for k, v in fields.items():
            setattr(u, k, v[:150])
        u.status, u.reject_reason, u.state = "approved", "", ""
        u.reviewed_by, u.reviewed_at = request.user, timezone.now()
        u.save()
        audit(request.user, "customer_approve", f"{u.full_name} (tg {u.tg_id})")
        sent = bot.notify(u, f"✅ {u.full_name}, arizangiz tasdiqlandi! Endi botdan foydalanishingiz mumkin.", bot.MENU)
        messages.success(request, f"{u.full_name} tasdiqlandi." + ("" if sent else " Telegramga xabar yuborib bo‘lmadi."))
        if not sent:
            messages.warning(request, "Foydalanuvchiga xabar yetib bormadi (bot tokenini va foydalanuvchi botni bloklamaganini tekshiring).")
    elif action == "reject":
        reason = (request.POST.get("reason") or "").strip()
        if len(reason) < 3:
            messages.error(request, "Bekor qilish sababini yozing.")
            return back
        u.status, u.reject_reason, u.state = "rejected", reason[:500], ""
        u.reviewed_by, u.reviewed_at = request.user, timezone.now()
        u.save()
        audit(request.user, "customer_reject", f"{u.full_name} (tg {u.tg_id}): {reason[:200]}")
        sent = bot.notify(u, f"❌ Arizangiz bekor qilindi.\n\nSabab: {reason}\n\nQayta ro‘yxatdan o‘tish uchun /start ni bosing.", bot.NO_KB)
        messages.success(request, f"{u.full_name} arizasi bekor qilindi.")
        if not sent:
            messages.warning(request, "Foydalanuvchiga xabar yetib bormadi.")
    elif action == "block" and u.status == "approved":
        u.status = "blocked"
        u.save(update_fields=["status"])
        audit(request.user, "customer_block", f"{u.full_name} (tg {u.tg_id})")
        messages.success(request, f"{u.full_name} bloklandi.")
    elif action == "unblock" and u.status == "blocked":
        u.status = "approved"
        u.save(update_fields=["status"])
        audit(request.user, "customer_unblock", f"{u.full_name} (tg {u.tg_id})")
        messages.success(request, f"{u.full_name} blokdan chiqarildi.")
    return back


@login_required
@view_perm
def template_file(request):
    resp = HttpResponse(rules.build_template(), content_type=XLSX)
    resp["Content-Disposition"] = 'attachment; filename="CUSTOMER_SHABLON.xlsx"'
    return resp


# ------------------------------------------------------------------ statistika
def _month(s: str):
    try:
        y, m = map(int, (s or "").split("-"))
        return date(y, m, 1)
    except (ValueError, TypeError):
        return None


def _shift_month(d: date, n: int) -> date:
    i = d.year * 12 + d.month - 1 + n
    return date(i // 12, i % 12 + 1, 1)


def _pct(ok, sent):
    return round(ok / sent * 100, 1) if sent else None


def _table(qs):
    """ETK kodlari bo‘yicha jadval (barcha 17 kod, noldagilari ham) + «noma’lum» + jami."""
    by = {r["etk_code"]: r for r in qs.values("etk_code").annotate(sent=Sum("sent"), ok=Sum("ok"), bad=Sum("bad")).order_by()}
    rows = []
    for code in (*rules.ETK_CODES, ""):
        r = by.get(code)
        if code == "" and not r:
            continue
        sent, ok, bad = (r["sent"], r["ok"], r["bad"]) if r else (0, 0, 0)
        rows.append({"etk": code or "Noma’lum", "unknown": code == "", "sent": sent, "ok": ok, "bad": bad, "pct": _pct(ok, sent)})
    tot = {k: sum(r[k] for r in rows) for k in ("sent", "ok", "bad")}
    tot["pct"] = _pct(tot["ok"], tot["sent"])
    return rows, tot


@login_required
@stats_perm
def stats(request):
    today = timezone.localdate()
    mode = "month" if request.GET.get("mode") == "month" else "day"
    ctx = {"mode": mode}
    if mode == "day":
        d = _day(request.GET.get("date")) or today
        rows, tot = _table(EtkStat.objects.filter(date=d))
        series = {r["date"]: r for r in EtkStat.objects.filter(date__gte=d - timedelta(days=13), date__lte=d)
                  .values("date").annotate(sent=Sum("sent"), ok=Sum("ok"), bad=Sum("bad")).order_by()}
        trend = []
        for i in range(14):
            day = d - timedelta(days=i)
            r = series.get(day, {"sent": 0, "ok": 0, "bad": 0})
            trend.append({"label": f"{day:%d.%m.%Y}", **{k: r[k] for k in ("sent", "ok", "bad")}, "pct": _pct(r["ok"], r["sent"]), "current": day == d})
        ctx.update(value=d.isoformat(), title=f"{d:%d.%m.%Y}", param="date", prev_val=(d - timedelta(days=1)).isoformat(),
                   next_val=(d + timedelta(days=1)).isoformat() if d < today else "", trend_title="Oxirgi 14 kun (kunlar bo‘yicha jami)")
    else:
        first = _month(request.GET.get("month")) or today.replace(day=1)
        nxt = _shift_month(first, 1)
        rows, tot = _table(EtkStat.objects.filter(date__gte=first, date__lt=nxt))
        # oxirgi 12 oy
        start = _shift_month(first, -11)
        months: dict[date, list[int]] = {}
        for r in EtkStat.objects.filter(date__gte=start, date__lt=nxt).values("date", "sent", "ok", "bad"):
            m = months.setdefault(r["date"].replace(day=1), [0, 0, 0])
            m[0] += r["sent"]; m[1] += r["ok"]; m[2] += r["bad"]   # noqa: E702
        trend = []
        for i in range(12):
            m = _shift_month(first, -i)
            s_, o_, b_ = months.get(m, [0, 0, 0])
            trend.append({"label": f"{m:%m.%Y}", "sent": s_, "ok": o_, "bad": b_, "pct": _pct(o_, s_), "current": m == first})
        ctx.update(value=f"{first:%Y-%m}", title=f"{first:%m.%Y}", param="month", prev_val=f"{_shift_month(first, -1):%Y-%m}",
                   next_val=f"{nxt:%Y-%m}" if nxt <= today.replace(day=1) else "", trend_title="Oxirgi 12 oy (oylar bo‘yicha jami)")
        # shu oyning kunlari
        days = {r["date"]: r for r in EtkStat.objects.filter(date__gte=first, date__lt=nxt).values("date")
                .annotate(sent=Sum("sent"), ok=Sum("ok"), bad=Sum("bad")).order_by()}
        ctx["days"] = [{"label": f"{k:%d.%m.%Y}", "sent": v["sent"], "ok": v["ok"], "bad": v["bad"], "pct": _pct(v["ok"], v["sent"])}
                       for k, v in sorted(days.items(), reverse=True)]
    ctx.update(rows=rows, tot=tot, trend=trend)
    return render(request, "customers/stats.html", ctx)
