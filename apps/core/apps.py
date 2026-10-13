"""App config for apps.core."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Shared abstractions used by every other app."""

    name = "apps.core"
    label = "core"
