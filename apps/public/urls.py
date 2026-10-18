"""URLs for the public site, the boards and saved boards (views.py, boards.py, saved.py)."""

from django.urls import path

from apps.public import boards, saved, views

app_name = "public"

urlpatterns = [
    path("", views.page, {"path": "/"}, name="home"),
    path("docs", views.page, {"path": "/docs"}, name="docs"),
    path("privacy", views.page, {"path": "/privacy"}, name="privacy"),
    path("terms", views.page, {"path": "/terms"}, name="terms"),
    path("favicon.ico", views.favicon),
    path("board", boards.pick, name="picker"),
    path("board/<str:station>", boards.show, name="board"),
    path("board/<str:station>/<str:then>", boards.show, name="board_then"),
    path("api/board/<str:station>", boards.feed),
    path("api/board/<str:station>/<str:then>", boards.feed),
    path("api/stations", boards.suggest),
    path("api/boards", saved.boards, name="saved_boards"),
    path("api/boards/opened", saved.boards_opened, name="saved_boards_opened"),
    path("api/boards/favourite", saved.boards_favourite, name="saved_boards_favourite"),
    path("api/boards/move", saved.boards_move, name="saved_boards_move"),
]
