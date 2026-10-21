"""Django settings for traintracker.

One module for every environment. Anything that differs comes from an
environment variable (or `.env` locally, via python-decouple); the
defaults suit local development, and `DJANGO_DEBUG=0` turns on the
production hardening that `manage.py check --deploy` asks for.

Django's tables live in the database's default schema. The timetable and
the Darwin usage counts each keep a schema of their own (TIMETABLE_SCHEMA,
USAGE_SCHEMA), so one DATABASE_URL serves both.
"""

from pathlib import Path
from urllib.parse import urlsplit

import dj_database_url
from decouple import Csv, config
from django.utils.csp import CSP
from mcp_auth.conf import (
    CHATGPT_REDIRECT_URI_PATTERNS,
    DEFAULT_CONSENT_FORM_ACTION,
    DEFAULT_REDIRECT_URI_PATTERNS,
    oauth2_settings,
)

from apps.core import analytics
from apps.pwa import conf as pwa_conf

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG: bool = config("DJANGO_DEBUG", default=True, cast=bool)
SECRET_KEY: str = config(
    "DJANGO_SECRET_KEY", default="django-insecure-local-development-only" if DEBUG else ""
)
ALLOWED_HOSTS: list[str] = config("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
CSRF_TRUSTED_ORIGINS: list[str] = config("DJANGO_CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

# Render sets this to the service's onrender.com name.
if RENDER_EXTERNAL_HOSTNAME := config("RENDER_EXTERNAL_HOSTNAME", default=""):
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")

# The site's public address, for links in emails and the passkey origin.
# On Render it defaults to the service's onrender.com name; set SITE_URL
# when a custom domain is added.
SITE_URL: str = config(
    "SITE_URL",
    default=f"https://{RENDER_EXTERNAL_HOSTNAME}"
    if RENDER_EXTERNAL_HOSTNAME
    else "http://localhost:8000",
).rstrip("/")

# Render terminates TLS and forwards plain HTTP with X-Forwarded-Proto set.
# Its health check calls /livez over plain HTTP, so the health checks are exempt.
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = config("DJANGO_SSL_REDIRECT", default=True, cast=bool)
    SECURE_REDIRECT_EXEMPT = [r"^livez$", r"^healthz$"]
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

SILENCED_SYSTEM_CHECKS = [
    # HSTS preload needs a registrable domain of our own; an onrender.com
    # name can't be preloaded.
    "security.W021",
    # ALLOW_LOCALHOST_LOOPBACK on purpose: Claude Code and other local MCP
    # clients sign in through a loopback port (RFC 8252); mcp_auth only
    # allows loopback callbacks that match its pattern.
    "oauth2_provider.W008",
]

# fivebar counts visits without cookies; off until FIVEBAR_DOMAIN is set.
# The sign-in pages never load it (one-time addresses). See apps/core/analytics.py.
FIVEBAR_DOMAIN: str = config("FIVEBAR_DOMAIN", default="")
# A fivebar token made to send events and nothing else: with FIVEBAR_DOMAIN,
# each MCP tool call is counted as a server event. See apps/mcp/events.py.
FIVEBAR_TOKEN: str = config("FIVEBAR_TOKEN", default="")

# Content Security Policy: everything from this origin, nothing inline. The
# other origin is fivebar's, and only while FIVEBAR_DOMAIN is set.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF, *analytics.csp_sources(FIVEBAR_DOMAIN)],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF, *analytics.csp_sources(FIVEBAR_DOMAIN)],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}
CSRF_FAILURE_VIEW = "apps.core.errors.csrf_failure"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.accounts",
    "apps.public",
    "apps.rail",
    "apps.mcp",
    # After the project's apps so their templates (templates/mcp_auth/) win.
    "mcp_auth",
    "oauth2_provider",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # After authentication: the replay fingerprint includes the user.
    "apps.core.idempotency.IdempotencyMiddleware",
    # After authentication: signed-in pages are never cached.
    "apps.core.private_pages.PrivatePagesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.public.context_processors.site",
                "apps.core.analytics.analytics",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# DATABASE_URL (Postgres in production) wins; otherwise a local SQLite file.
if DATABASE_URL := config("DATABASE_URL", default=""):
    DATABASES = {
        "default": dj_database_url.parse(DATABASE_URL, conn_max_age=600, conn_health_checks=True)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Sign-in is by emailed link or code, or a passkey; there are no passwords
# outside the admin's own (unused) form. See docs/accounts.md. Nobody signs
# in to Traintrackr for its own sake: it happens on the way to connecting
# Claude or ChatGPT, and the account page is where that ends otherwise.
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:sign_in"
LOGIN_REDIRECT_URL = "accounts:account"
LOGOUT_REDIRECT_URL = "accounts:sign_in"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 90

# How long an emailed sign-in link and code work, and how many wrong codes
# end a request.
SIGN_IN_MAX_AGE_SECONDS = 15 * 60
SIGN_IN_MAX_CODE_ATTEMPTS = 5

# No email address is stored: an account is found by the address's key, an
# HMAC made with this secret, and shows a masked address (h•••@example.com).
# It is not SECRET_KEY, so that one can be rotated freely; changing or
# losing this one cuts every account off, so keep a copy. titan makes it.
# Set ACCOUNTS_KEEP_EMAIL to keep the address as well, for an app that has
# to email people later; the privacy notice follows it. See
# docs/decisions/accounts-keep-a-key-not-an-email-address.md.
ACCOUNTS_EMAIL_KEY: str = config(
    "ACCOUNTS_EMAIL_KEY", default="insecure-local-development-only" if DEBUG else ""
)
ACCOUNTS_KEEP_EMAIL = False

# Passkeys (WebAuthn). The relying party is this site's host name; a
# passkey only works on the host it was made for.
WEBAUTHN_RP_ID: str = config("WEBAUTHN_RP_ID", default=urlsplit(SITE_URL).hostname or "localhost")
WEBAUTHN_RP_NAME = pwa_conf.APP_NAME
WEBAUTHN_ORIGINS: list[str] = config("WEBAUTHN_ORIGINS", default=SITE_URL, cast=Csv())

# Email. The console in development; any SMTP relay in production (Django
# 6.1's MAILERS replaces the EMAIL_HOST family of settings).
_SMTP = "django.core.mail.backends.smtp.EmailBackend"
_EMAIL_BACKEND: str = config(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend" if DEBUG else _SMTP
)
MAILERS = {
    "default": {
        "BACKEND": _EMAIL_BACKEND,
        "OPTIONS": {
            "host": config("EMAIL_HOST", default="localhost"),
            "port": config("EMAIL_PORT", default=587, cast=int),
            "username": config("EMAIL_HOST_USER", default=""),
            "password": config("EMAIL_HOST_PASSWORD", default=""),
            "use_tls": config("EMAIL_USE_TLS", default=True, cast=bool),
            "timeout": 10,
        }
        if _EMAIL_BACKEND == _SMTP
        else {},
    }
}
DEFAULT_FROM_EMAIL: str = config(
    "DEFAULT_FROM_EMAIL",
    default=f"{pwa_conf.APP_NAME} <noreply@localhost>",
)

# Background tasks (django.tasks). Email is sent from a task so a slow mail
# server never holds a request: the thread backend runs each task on its own
# thread in this process and returns at once (tests run them inline). Swap in
# a queue-backed backend (and a worker) when the volume needs one.
TASKS = {"default": {"BACKEND": "apps.core.task_backends.ThreadBackend"}}

# Rate limits count per client address. Behind Render's proxy the client is
# the last X-Forwarded-For entry; locally there is no proxy.
RATE_LIMIT_PROXY_COUNT: int = config("RATE_LIMIT_PROXY_COUNT", default=0 if DEBUG else 1, cast=int)

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        # httpx logs every request URL at INFO. The Network Rail download
        # (refresh_timetable) redirects to a presigned S3 URL whose query string
        # holds temporary AWS credentials.
        "httpx": {"level": "WARNING"},
        "httpcore": {"level": "WARNING"},
        # The MCP SDK logs each failed tool call at INFO with the error text,
        # which names what was asked for ("'Yeovil' is ambiguous"). The privacy
        # policy says questions are not written to logs.
        "mcp.server.mcpserver.server": {"level": "WARNING"},
    },
}

# OAuth 2.1 for the MCP endpoint, so Claude and ChatGPT can add Traintrackr
# as a connector. The shared Titan preset: PKCE, rotating refresh tokens, DCR
# and CIMD, held to the callbacks below.
OAUTH2_PROVIDER = oauth2_settings(
    resource_name=pwa_conf.APP_NAME,
    scope_description="Look up train times",
)
MCP_AUTH = {
    # Anyone with an active account may connect, not only staff (the
    # template's default): the tools only read public rail data, and
    # Traintrackr is listed in the Claude and ChatGPT directories.
    "CAN_CONNECT": "mcp_auth.policy.active_user",
    # The consent page's "Signed in as": the masked address, not the key.
    "ACCOUNT_NAME": "apps.accounts.models.account_name",
    # ChatGPT's callbacks as well as Claude's and loopback.
    "REDIRECT_URI_PATTERNS": DEFAULT_REDIRECT_URI_PATTERNS + CHATGPT_REDIRECT_URI_PATTERNS,
    "CONSENT_FORM_ACTION": [
        *DEFAULT_CONSENT_FORM_ACTION,
        "https://chatgpt.com",
        "https://platform.openai.com",
    ],
}
# The token OpenAI's platform issues to verify the domain before ChatGPT's
# app directory lists the MCP server: off (a 404) unless set.
OPENAI_APPS_CHALLENGE: str = config("OPENAI_APPS_CHALLENGE", default="")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
