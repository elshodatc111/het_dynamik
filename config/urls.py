from django.contrib import admin
from django.urls import include, path

from apps.core import jobs, views as core_views
from django.contrib.auth.decorators import login_required

urlpatterns = [
    path("jobs/<str:job_id>/", login_required(lambda request, job_id: jobs.status_response(request, job_id)), name="job_status"),
    path("sozlamalar/", core_views.settings_view, name="site_settings"),
    path("sozlamalar/telegram-test/", core_views.telegram_check, name="site_telegram_check"),
    path("tizim-holati/", core_views.system_status, name="system_status"),
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.reports.urls")),
    path("", include("apps.monitoring.urls")),
    path("", include("apps.customers.urls")),
    path("", include("apps.locations.urls")),
    path("", include("apps.broadcasts.urls")),
    path("", include("apps.core.pwa_urls")),
]
