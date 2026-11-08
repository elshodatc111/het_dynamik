from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password

from apps.reports.services import PERMS, ROLES

User = get_user_model()


class UserForm(forms.ModelForm):
    role = forms.ChoiceField(label="Rol", required=False)
    extra_perms = forms.MultipleChoiceField(
        label="Qo‘shimcha ruxsatlar", required=False, choices=[(k, v) for k, v in PERMS.items()],
        widget=forms.CheckboxSelectMultiple,
    )
    password1 = forms.CharField(label="Parol", required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Parolni takrorlang", required=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]
        labels = {
            "username": "Login", "first_name": "Ism", "last_name": "Familiya",
            "email": "Email", "is_active": "Faol (tizimga kira oladi)",
        }

    def __init__(self, *args, editing_self=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.editing_self = editing_self
        self.fields["role"].choices = [("", "— rolsiz —")] + [
            (g.name, f"{g.name} — {ROLES[g.name][0]}" if g.name in ROLES else g.name) for g in Group.objects.order_by("name")]
        if self.instance.pk:
            grp = self.instance.groups.first()
            self.fields["role"].initial = grp.name if grp else ""
            self.fields["extra_perms"].initial = [
                p.codename for p in self.instance.user_permissions.filter(content_type__app_label="reports")
            ]
        else:
            self.fields["password1"].required = True
            self.fields["password2"].required = True
            self.fields["is_active"].initial = True
        if editing_self:  # o‘zini bloklab qo‘ymaslik uchun
            for f in ("role", "extra_perms", "is_active"):
                self.fields[f].disabled = True

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 or p2:
            if p1 != p2:
                self.add_error("password2", "Parollar bir xil emas.")
            else:
                try:
                    validate_password(p1, self.instance if self.instance.pk else None)
                except forms.ValidationError as exc:
                    self.add_error("password1", exc)
        return data


class RoleForm(forms.Form):
    name = forms.CharField(label="Rol nomi", max_length=60)
    perms = forms.MultipleChoiceField(label="Ruxsatlar", required=False, choices=[(k, v) for k, v in PERMS.items()],
                                      widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        if group is not None:
            self.fields["name"].initial = group.name
            self.fields["perms"].initial = list(group.permissions.filter(content_type__app_label="reports").values_list("codename", flat=True))
            if group.name in ROLES:            # tayyor rol nomi o‘zgarmaydi
                self.fields["name"].disabled = True

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if self.group is not None and self.group.name in ROLES:
            return self.group.name
        if not name:
            raise forms.ValidationError("Rol nomini yozing.")
        qs = Group.objects.filter(name__iexact=name)
        if self.group is not None:
            qs = qs.exclude(pk=self.group.pk)
        if qs.exists():
            raise forms.ValidationError("Bunday nomli rol bor.")
        return name
