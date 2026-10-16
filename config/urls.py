"""Root URL configuration."""

from django.contrib import admin
from django.urls import include, path

from apps.accounts.views import admin_login
from apps.core.views import healthz, livez
from apps.mcp.views import mcp, openai_apps_challenge
from apps.pwa.conf import APP_NAME

admin.site.site_header = APP_NAME
admin.site.site_title = APP_NAME

urlpatterns = [
    # Public: the homepage, docs, privacy, terms and the departure boards.
    path("", include("apps.public.urls")),
    # Signing in and out, and the account page.
    path("", include("apps.accounts.urls")),
    # Health checks: /livez for Render (process up), /healthz adds the database.
    path("livez", livez, name="livez"),
    path("healthz", healthz, name="healthz"),
    # Staff sign in like everyone else; the admin's password form is unused.
    path("admin/login/", admin_login),
    path("admin/", admin.site.urls),
    # OAuth for MCP clients, and the endpoint itself.
    path("", include("mcp_auth.urls")),
    path("mcp", mcp, name="mcp"),
    # OpenAI's domain check for ChatGPT's app directory.
    path(".well-known/openai-apps-challenge", openai_apps_challenge, name="openai-apps-challenge"),
]
