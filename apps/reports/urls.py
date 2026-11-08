from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("dinamika/", views.dynamics, name="dynamics"),
    path("solishtirish/", views.compare, name="compare"),
    path("solishtirish/excel/", views.compare_export, name="compare_export"),
    path("trend/", views.trend, name="trend"),
    path("trend/data/", views.trend_data, name="trend_data"),
    path("yuklash/", views.upload, name="upload"),
    path("yuklash/natija/<str:job_id>/", views.upload_result, name="upload_result"),
    path("yuklash/saqlandi/<str:job_id>/", views.upload_done, name="upload_done"),
    path("yuklash/tasdiqlash/", views.upload_confirm, name="upload_confirm"),
    path("arxiv/", views.archive, name="archive"),
    path("arxiv/<int:pk>/pdf/", views.upload_file, name="archive_file"),
    path("arxiv/<int:pk>/ochirish/", views.upload_delete, name="archive_delete"),
    path("audit/", views.audit, name="audit"),
]
