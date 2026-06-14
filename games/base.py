# -*- coding: utf-8 -*-
"""Foundation for the modular game system.

- ``GameResult``  — a small value object describing the outcome of a round.
- ``SessionStore`` — a per-user in-memory session container that is a drop-in
  replacement for the legacy global session dicts (it behaves exactly like a
  ``dict``), plus an optional per-key ``asyncio.Lock``.
- ``BaseGame``    — the protocol every game module satisfies via
  ``register_handlers(app)``.

Nothing here changes game behaviour; it is additive scaffolding that game
modules migrate onto incrementally.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable


@dataclass
class GameResult:
    """Outcome of a single game round."""

    won: bool
    bet: float
    payout: float = 0.0          # total returned to player (0 = lost stake)
    multiplier: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


class SessionStore(MutableMapping):
    """Dict-like store of active sessions keyed by user_id (or any hashable key).

    Drop-in for the legacy ``{}`` session globals: supports ``store[k]``,
    ``store.get``, ``store.pop``, ``k in store``, iteration, ``len`` and
    ``del``. ``lock(key)`` exposes a per-key ``asyncio.Lock`` for serializing a
    user's concurrent callbacks.
    """

    def __init__(self) -> None:
        self._data: dict[Any, Any] = {}
        self._locks: dict[Any, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- MutableMapping interface (faithful dict semantics) ---
    def __getitem__(self, key: Any) -> Any:
        return self._data[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: Any) -> None:
        del self._data[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: Any) -> bool:  # dict-fast path
        return key in self._data

    def __repr__(self) -> str:
        return f"SessionStore({self._data!r})"

    # --- extras ---
    def lock(self, key: Any) -> asyncio.Lock:
        """Return the per-key asyncio lock (created on first use)."""
        return self._locks[key]


@runtime_checkable
class BaseGame(Protocol):
    """Every game module exposes this so the orchestrator can wire it up."""

    def register_handlers(self, app: Any) -> None:
        ...
