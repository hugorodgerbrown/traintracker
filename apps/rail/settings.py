"""The MCP server's settings, read the way ``traintracker`` reads them.

Its settings (DATABASE_URL, DARWIN_API_KEY, NR_USERNAME, ...) are its own
environment variables, documented in the README and ``.env.example``, not
Django settings: production reads them from the same environment until the
cut-over, and both must see the same values.
"""

from traintracker.config import Settings, load_dotenv


def rail_settings() -> Settings:
    """Load ``.env`` (unless TRAINTRACKER_NO_DOTENV is set) and read the settings."""
    load_dotenv()
    return Settings.from_env()
