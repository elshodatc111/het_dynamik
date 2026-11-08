"""Butun loyiha sozlamalari: Telegram bot tokeni barcha bo‘limlar (Monitoring va boshqalar) uchun umumiy."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.monitoring import telegram
from apps.reports.services import audit

from .models import get_site

admin_perm = permission_required("reports.manage_settings", raise_exception=True)
status_perm = permission_required("reports.view_status", raise_exception=True)


@login_required
@admin_perm
def settings_view(request):
    site = get_site()
    if request.method == "POST":
        new_token = request.POST.get("telegram_token", "").strip()
        clear = bool(request.POST.get("clear_token"))
        if new_token and not telegram.valid_token(new_token):
            messages.error(request, "Bot tokeni noto‘g‘ri ko‘rinishda. @BotFather bergan to‘liq tokenni kiriting (masalan 123456789:AAH...).")
        elif not new_token and not clear:
            messages.info(request, "O‘zgarish kiritilmadi.")
            return redirect("site_settings")
        else:
            if new_token:
                site.telegram_token = telegram.encrypt_token(new_token)
                note = "token yangilandi"
            else:
                site.telegram_token = ""
                note = "token o‘chirildi"
            site.save()
            audit(request.user, "site_settings", f"Telegram: {note}")
            messages.success(request, "Sozlamalar saqlandi.")
            if new_token:
                try:
                    bot = telegram.get_me()
                    messages.success(request, f"Bot tokeni tasdiqlandi: @{bot.get('username', '?')}")
                except telegram.TelegramError as exc:
                    messages.warning(request, f"Token saqlandi, lekin Telegram tasdiqlamadi: {exc}")
            return redirect("site_settings")
    return render(request, "settings.html", {
        "token_source": telegram.token_source(), "token_mask": telegram.mask_token(),
        "token_broken": bool(site.telegram_token) and telegram.token_source() != "db",
    })


@login_required
@admin_perm
@require_POST
def telegram_check(request):
    """Saqlangan token ishlayaptimi — Telegramdan bot nomini so‘raydi."""
    if not telegram.is_configured():
        messages.error(request, telegram.NO_TOKEN_MSG)
    else:
        try:
            bot = telegram.get_me()
            messages.success(request, f"Bot ishlayapti: @{bot.get('username', '?')} ({bot.get('first_name', '')}).")
        except telegram.TelegramError as exc:
            messages.error(request, str(exc))
    return redirect("site_settings")


@login_required
@status_perm
def system_status(request):
    from . import status

    return render(request, "status.html", {"res": status.run_checks(force_tg=request.GET.get("tg") == "1")})
