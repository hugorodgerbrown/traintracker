"""App config for apps.rail."""

from django.apps import AppConfig


class RailConfig(AppConfig):
    """The timetable, Darwin and the journey planner, as Django runs them."""

    name = "apps.rail"
    label = "rail"
