from django.contrib.auth.signals import user_logged_in, user_login_failed, user_logged_out
from django.dispatch import receiver

from .services import audit


@receiver(user_logged_in)
def _on_login(sender, request, user, **kw):
    audit(user, "login", f"IP: {request.META.get('REMOTE_ADDR', '')}")


@receiver(user_logged_out)
def _on_logout(sender, request, user, **kw):
    if user:
        audit(user, "logout")


@receiver(user_login_failed)
def _on_fail(sender, credentials, request=None, **kw):
    audit(None, "login_failed", f"login: {credentials.get('username', '')}")
