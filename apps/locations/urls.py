from django.urls import path

from . import views

urlpatterns = [
    path("location/", views.data, name="location_data"),
    path("location/xarita/", views.map_page, name="location_map"),
    path("location/xarita/nuqtalar/", views.map_points, name="location_points"),
    path("location/yuklash/", views.upload, name="location_upload"),
    path("location/yuklashlar/", views.uploads, name="location_uploads"),
    path("location/yuklashlar/<int:pk>/xatolar/", views.upload_errors, name="location_upload_errors"),
    path("location/shablon/", views.template_file, name="location_template"),
    path("location/eksport/", views.export, name="location_export"),
    path("location/qidiruv-jurnali/", views.searches, name="location_searches"),
    path("location/qidiruv-jurnali/excel/", views.searches_export, name="location_searches_export"),
    path("location/<int:pk>/", views.place_detail, name="location_detail"),
    path("location/<int:pk>/ochirish/", views.place_delete, name="location_delete"),
]
