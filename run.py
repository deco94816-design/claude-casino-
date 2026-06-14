#!/usr/bin/env python
"""Convenience launcher: verify dependencies, then start the bot.

Equivalent to running ``python -m optimus`` directly, with a friendlier
pre-flight check. The bot itself is defined in the ``optimus`` package; this
script never executes ``librate_casino.py`` as a script (see optimus/__main__).
"""

import subprocess
import sys

REQUIRED = {
    "telegram": "python-telegram-bot[job-queue]",
    "httpx": "httpx",
    "PIL": "Pillow",
    "dotenv": "python-dotenv",
}


def ensure_dependencies() -> None:
    missing = []
    for module, package in REQUIRED.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print(f"Installing missing packages: {', '.join(missing)}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", *missing, "-q"])


def main() -> int:
    print("Telegram Casino Bot — starting…")
    ensure_dependencies()
    return subprocess.call([sys.executable, "-m", "optimus"])


if __name__ == "__main__":
    sys.exit(main())
