"""Canonical entrypoint:  ``python -m optimus``

This imports the bot runtime as the ``librate_casino`` module (NOT as ``__main__``)
and calls ``main()`` explicitly. Importing it under a stable module name is essential:
the game modules in ``games/`` do ``import librate_casino as lc``, so there must be
exactly one ``librate_casino`` module instance for shared state to stay consistent.
Running ``librate_casino.py`` directly would load it as ``__main__`` and create a
second, state-isolated copy — which is why that path now fails fast.
"""

import librate_casino


def main() -> None:
    librate_casino.main()


if __name__ == "__main__":
    main()
