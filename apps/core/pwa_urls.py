from django.urls import path

from . import pwa

urlpatterns = [
    path("manifest.webmanifest", pwa.manifest, name="pwa_manifest"),
    path("sw.js", pwa.service_worker, name="pwa_sw"),
    path("offline/", pwa.offline, name="pwa_offline"),
]
