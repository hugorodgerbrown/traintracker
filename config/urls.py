"""Root URL configuration."""

from django.contrib import admin
from django.urls import path

from apps.core.views import healthz, livez

admin.site.site_header = "Traintrackr"
admin.site.site_title = "Traintrackr"

urlpatterns = [
    # Health checks: /livez for Render (process up), /healthz adds the database.
    path("livez", livez, name="livez"),
    path("healthz", healthz, name="healthz"),
    path("admin/", admin.site.urls),
]
