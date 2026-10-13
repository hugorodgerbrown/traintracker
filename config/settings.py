"""Django settings for traintracker.

One module for every environment. Anything that differs comes from an
environment variable (or `.env` locally, via python-decouple); the
defaults suit local development, and `DJANGO_DEBUG=0` turns on the
production hardening that `manage.py check --deploy` asks for.

Traintrackr is moving onto the Titan template in four steps
(docs/decisions/traintrackr-moves-onto-titan-in-four-steps.md). Until the
last one, production still runs `traintracker serve-http` (src/traintracker)
and nothing here is deployed. The accounts, public pages, design page and
MCP endpoint, and the settings that go with them, arrive with the steps
that bring them.

Django's tables live in the database's default schema. The timetable, the
Darwin usage counts and the current sign-in each keep a schema of their own
(TIMETABLE_SCHEMA, USAGE_SCHEMA, MCP_AUTH_SCHEMA), so one DATABASE_URL
serves both.
"""

from pathlib import Path

import dj_database_url
from decouple import Csv, config
from django.utils.csp import CSP

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
]

# Content Security Policy: everything from this origin, nothing inline.
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "object-src": [CSP.NONE],
    "base-uri": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
}
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"
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
    "apps.rail",
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
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
