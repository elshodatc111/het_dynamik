from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("kirish/", auth_views.LoginView.as_view(template_name="login.html", redirect_authenticated_user=True), name="login"),
    path("chiqish/", auth_views.LogoutView.as_view(), name="logout"),
    path("parol/", views.password_change, name="password_change"),
    path("foydalanuvchilar/", views.users, name="users"),
    path("foydalanuvchilar/yangi/", views.user_create, name="users_new"),
    path("foydalanuvchilar/<int:pk>/", views.user_edit, name="users_edit"),
    path("foydalanuvchilar/<int:pk>/holat/", views.user_toggle, name="users_toggle"),
    path("rollar/", views.roles, name="roles"),
    path("rollar/yangi/", views.role_edit, name="role_new"),
    path("rollar/<int:pk>/", views.role_edit, name="role_edit"),
    path("rollar/<int:pk>/ochirish/", views.role_delete, name="role_delete"),
]
