from django.urls import path

from . import views

urlpatterns = [
    path("customer/", views.data, name="customer_data"),
    path("customer/yuklab-olish/", views.export, name="customer_export"),
    path("customer/statistika/", views.stats, name="customer_stats"),
    path("customer/jurnal/", views.journal, name="customer_journal"),
    path("customer/arizalar/", views.applications, name="customer_apps"),
    path("customer/arizalar/<int:pk>/", views.application_action, name="customer_app_action"),
    path("customer/shablon/", views.template_file, name="customer_template"),
]
