"""The train tools' settings, read from the environment.

Their settings (DATABASE_URL, DARWIN_API_KEY, NR_USERNAME, ...) are their own
environment variables, documented in the README and ``.env.example``, not
Django settings: apps/rail/config.py reads them.
"""

from apps.rail.config import Settings, load_dotenv


def rail_settings() -> Settings:
    """Load ``.env`` (unless TRAINTRACKER_NO_DOTENV is set) and read the settings."""
    load_dotenv()
    return Settings.from_env()
