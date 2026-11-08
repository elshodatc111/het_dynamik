from django.urls import path

from . import views

urlpatterns = [
    path("monitoring/", views.dashboard, name="mon_dashboard"),
    path("monitoring/yuklash/", views.upload, name="mon_upload"),
    path("monitoring/yuklash/natija/<str:job_id>/", views.upload_result, name="mon_upload_result"),
    path("monitoring/yuklash/tasdiqlash/", views.upload_confirm, name="mon_upload_confirm"),
    path("monitoring/solishtirish/", views.compare, name="mon_compare"),
    path("monitoring/arxiv/", views.archive, name="mon_archive"),
    path("monitoring/sozlamalar/", views.settings_view, name="mon_settings"),
    path("monitoring/sozlamalar/test/", views.settings_test, name="mon_settings_test"),
    path("monitoring/sana/<str:day>/", views.snapshot_by_date, name="mon_snapshot_by_date"),
    path("monitoring/<int:pk>/", views.snapshot, name="mon_snapshot"),
    path("monitoring/<int:pk>/konsentratorlar/", views.dcu_list, name="mon_dcu"),
    path("monitoring/<int:pk>/hisoblagichlar/", views.meter_list, name="mon_meters"),
    path("monitoring/<int:pk>/svod.png", views.svod_image, name="mon_svod_image"),
    path("monitoring/<int:pk>/yuklab/<str:kind>/", views.download, name="mon_download"),
    path("monitoring/<int:pk>/yuborish/", views.send, name="mon_send"),
    path("monitoring/<int:pk>/ochirish/", views.delete, name="mon_delete"),
]
