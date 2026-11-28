"""URLs for pairing: the TV app's endpoints under /tv/pair, the phone's page at /tv."""

from django.urls import path

from apps.pairing import views

app_name = "pairing"

urlpatterns = [
    path("tv", views.pair, name="pair"),
    path("tv/pair", views.tv_start, name="start"),
    path("tv/pair/<str:token>", views.tv_poll, name="poll"),
]
