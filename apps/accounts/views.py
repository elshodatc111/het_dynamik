from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.reports.perms import PERM_GROUPS
from apps.reports.services import PERMS, ROLES, audit

User = get_user_model()
manage = permission_required("reports.manage_users", raise_exception=True)


def _admins_left(excluding_pk=None):
    """Foydalanuvchilarni boshqara oladigan faol foydalanuvchilar soni (o‘zidan tashqari)."""
    n = 0
    for u in User.objects.filter(is_active=True):
        if u.pk != excluding_pk and u.has_perm("reports.manage_users"):
            n += 1
    return n


def _apply(form, user):
    """Rol va qo‘shimcha ruxsatlarni foydalanuvchiga qo‘llaydi."""
    if form.editing_self:
        return
    role = form.cleaned_data.get("role")
    user.groups.clear()
    if role:
        user.groups.add(Group.objects.get(name=role))
    perms = Permission.objects.filter(content_type__app_label="reports", codename__in=form.cleaned_data.get("extra_perms", []))
    user.user_permissions.set(perms)


def _grouped(form, field="extra_perms"):
    """Ruxsatlar bo‘limlar bo‘yicha: [(bo‘lim, [(kod, nomi, belgilangan)])]."""
    chosen = set(form[field].value() or [])
    return [(title, [(c, label, c in chosen) for c, label in items]) for title, items in PERM_GROUPS]


def _role_cards():
    out = []
    for g in Group.objects.order_by("name"):
        out.append({"pk": g.pk, "name": g.name, "desc": ROLES[g.name][0] if g.name in ROLES else "Maxsus rol",
                    "builtin": g.name in ROLES, "locked": g.name == "Admin",
                    "perms": g.permissions.filter(content_type__app_label="reports").count(), "users": g.user_set.count()})
    return out


@login_required
@manage
def users(request):
    rows = []
    for u in User.objects.order_by("-is_active", "username"):
        grp = u.groups.first()
        extra = list(u.user_permissions.filter(content_type__app_label="reports").values_list("codename", flat=True))
        rows.append({"user": u, "role": grp.name if grp else ("Superuser" if u.is_superuser else "—"),
                     "extra": [PERMS[c] for c in extra if c in PERMS]})
    return render(request, "users/list.html", {"rows": rows, "roles": _role_cards()})


@login_required
@manage
def user_create(request):
    from .forms import UserForm

    form = UserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data["password1"])
            user.save()
            _apply(form, user)
        audit(request.user, "user_create", f"{user.username} · rol: {form.cleaned_data.get('role') or '—'}")
        messages.success(request, f"«{user.username}» foydalanuvchisi yaratildi.")
        return redirect("users")
    return render(request, "users/form.html", {"form": form, "obj": None, "perm_groups": _grouped(form)})


@login_required
@manage
def user_edit(request, pk):
    from .forms import UserForm

    obj = get_object_or_404(User, pk=pk)
    me = obj.pk == request.user.pk
    form = UserForm(request.POST or None, instance=obj, editing_self=me)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save(commit=False)
            if form.cleaned_data.get("password1"):
                user.set_password(form.cleaned_data["password1"])
            user.save()
            _apply(form, user)
            if not me and not _admins_left():
                transaction.set_rollback(True)
                messages.error(request, "Kamida bitta faol administrator qolishi kerak.")
                return redirect("users_edit", pk=pk)
        audit(request.user, "user_update", f"{user.username} · faol: {user.is_active} · rol: {form.cleaned_data.get('role') or '—'}")
        messages.success(request, "O‘zgarishlar saqlandi.")
        return redirect("users")
    return render(request, "users/form.html", {"form": form, "obj": obj, "me": me, "perm_groups": _grouped(form),
                                               "effective": sorted(c for c in PERMS if obj.has_perm(f"reports.{c}"))})


@login_required
@manage
@require_POST
def user_toggle(request, pk):
    obj = get_object_or_404(User, pk=pk)
    if obj.pk == request.user.pk:
        messages.error(request, "O‘zingizni o‘chirib qo‘ya olmaysiz.")
        return redirect("users")
    if obj.is_active and obj.has_perm("reports.manage_users") and not _admins_left(excluding_pk=obj.pk):
        messages.error(request, "Kamida bitta faol administrator qolishi kerak.")
        return redirect("users")
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active"])
    audit(request.user, "user_toggle", f"{obj.username} → {'faol' if obj.is_active else 'o‘chirilgan'}")
    messages.success(request, f"«{obj.username}» {'faollashtirildi' if obj.is_active else 'o‘chirildi (kira olmaydi)'}.")
    return redirect("users")


@login_required
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    for f in form.fields.values():
        f.widget.attrs.setdefault("class", "")
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        audit(request.user, "password_change", "")
        messages.success(request, "Parol almashtirildi.")
        return redirect("dashboard")
    return render(request, "users/password.html", {"form": form})


# ------------------------------------------------------------------ rollar
manage_roles = permission_required("reports.manage_roles", raise_exception=True)


@login_required
@manage_roles
def roles(request):
    return render(request, "users/roles.html", {"roles": _role_cards()})


def _save_role(form, group):
    perms = Permission.objects.filter(content_type__app_label="reports", codename__in=form.cleaned_data["perms"])
    if group is None:
        group = Group.objects.create(name=form.cleaned_data["name"])
    elif group.name not in ROLES:
        group.name = form.cleaned_data["name"]
        group.save()
    group.permissions.set(perms)
    return group


@login_required
@manage_roles
def role_edit(request, pk=None):
    from .forms import RoleForm

    group = get_object_or_404(Group, pk=pk) if pk else None
    if group is not None and group.name == "Admin":
        messages.error(request, "«Admin» roli har doim barcha ruxsatlarga ega — o‘zgartirib bo‘lmaydi.")
        return redirect("roles")
    form = RoleForm(request.POST or None, group=group)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            group = _save_role(form, group)
        audit(request.user, "role_save", f"{group.name}: {len(form.cleaned_data['perms'])} ta ruxsat")
        messages.success(request, f"«{group.name}» roli saqlandi.")
        return redirect("roles")
    return render(request, "users/role_form.html", {"form": form, "obj": group, "perm_groups": _grouped(form, "perms")})


@login_required
@manage_roles
@require_POST
def role_delete(request, pk):
    group = get_object_or_404(Group, pk=pk)
    if group.name in ROLES:
        messages.error(request, "Tayyor rollarni o‘chirib bo‘lmaydi.")
    elif group.user_set.exists():
        messages.error(request, "Bu rolda foydalanuvchilar bor. Avval ularning rolini almashtiring.")
    else:
        name = group.name
        group.delete()
        audit(request.user, "role_delete", name)
        messages.success(request, f"«{name}» roli o‘chirildi.")
    return redirect("roles")
