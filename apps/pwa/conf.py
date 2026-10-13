"""The app's identity as the manifest, launch screen and browser chrome show it.

Set once by the project template from its answers; edit freely. Settings,
the admin and the MCP server read the name from here, and ``bin/make-icons``
drew the icons in ``static/icons/`` from the same name and colour.
"""

APP_NAME = "Traintrackr"
SHORT_NAME = APP_NAME[:12]
DESCRIPTION = "Live GB train times, platforms and journeys, in Claude and ChatGPT"
THEME_COLOUR = "#1d4ed8"
BACKGROUND_COLOUR = "#ffffff"

# The installed app is everything under this path. The service worker
# controls it and nothing else: the public pages, sign-in, admin, OAuth and
# MCP sit outside it and are never cached or shown inside the app.
SCOPE = "/app/"

# Prefix for every Cache Storage name the service worker owns, so it never
# deletes another app's caches on the same origin.
CACHE_PREFIX = "traintracker"

# Paths inside SCOPE the service worker must never cache (it already
# ignores everything outside SCOPE, and every non-GET request).
NEVER_CACHE: list[str] = []

# Static files fetched when the service worker installs, so the shell works
# offline from the first launch. Keep this list small: tests/test_performance.py
# holds it to a byte budget.
PRECACHE_STATIC = [
    "css/app.css",
    "vendor/htmx.min.js",
    "js/launch_gate.js",
    "js/launch_shell.js",
    "js/idb.js",
    "js/outbox_core.js",
    "js/outbox.js",
    "js/pwa.js",
    "js/notes.js",
    "icons/icon-192.png",
]

# Loaded into the service worker with importScripts(), in this order.
WORKER_SCRIPTS = [
    "js/idb.js",
    "js/outbox_core.js",
    "js/outbox.js",
    "js/sw_core.js",
    "js/sw_worker.js",
]
