"""Runtime configuration — secrets and bot identity loaded from the environment.

Secrets must come from a local ``.env`` file (see ``.env.example``); they are no
longer hardcoded in source. Loading is idempotent — ``auto_deposit`` also calls
``load_dotenv()`` — so importing this module early is safe.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- Secrets / identity (from .env) ---------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8311802199"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "Librate")
