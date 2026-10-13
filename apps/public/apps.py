"""App config for apps.public."""

from django.apps import AppConfig


class PublicConfig(AppConfig):
    """The public site: the homepage, docs, privacy, terms and the boards."""

    name = "apps.public"
    label = "public"
