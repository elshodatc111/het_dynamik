from django.urls import path

from . import views

urlpatterns = [
    path("postlar/", views.posts, name="posts"),
    path("postlar/yangi/", views.post_new, name="post_new"),
    path("postlar/<int:pk>/", views.post_detail, name="post_detail"),
    path("postlar/<int:pk>/eslatma/", views.post_remind, name="post_remind"),
    path("postlar/<int:pk>/ochirish/", views.post_delete, name="post_delete"),
    path("postlar/<int:pk>/excel/", views.post_export, name="post_export"),
]
