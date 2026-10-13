"""URLs for the public site and the boards (views.py, boards.py)."""

from django.urls import path

from apps.public import boards, views

app_name = "public"

urlpatterns = [
    path("", boards.home, name="home"),
    path("docs", views.page, {"path": "/docs"}, name="docs"),
    path("privacy", views.page, {"path": "/privacy"}, name="privacy"),
    path("terms", views.page, {"path": "/terms"}, name="terms"),
    path("favicon.ico", views.favicon),
    path(".well-known/openai-apps-challenge", views.openai_challenge),
    path("board", boards.pick, name="picker"),
    path("board/<str:station>", boards.show, name="board"),
    path("board/<str:station>/<str:then>", boards.show, name="board_then"),
    path("api/board/<str:station>", boards.feed),
    path("api/board/<str:station>/<str:then>", boards.feed),
    path("api/stations", boards.suggest),
]
