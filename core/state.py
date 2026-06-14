# -*- coding: utf-8 -*-
"""Shared mutable runtime state.

The monolith historically kept ~50 module-level globals that handlers rebound
via ``global``. They are migrated here onto a single ``State`` object so logic
can move out of ``librate_casino`` while keeping ONE source of truth. Fields are
added as each subsystem migrates; this module starts with the economic state the
wallet needs.

Defaults mirror the original module-level values in ``librate_casino`` exactly,
so behaviour is unchanged. ``stars_to_usd`` lives here (not in ``config``)
because admins can change it at runtime — it is state, not a constant.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional


class State:
    def __init__(self) -> None:
        # --- conversion / economics (mirrors librate_casino defaults) ---
        self.stars_to_usd: float = 0.0179          # STARS_TO_USD
        self.casino_bankroll_usd: float = 0.0      # casino_bankroll_usd
        self.bankroll_floor_usd: float = 10000.0   # hard floor in adjust_bankroll_usd

        # --- jackpot ---
        self.active_jackpot_stars: float = 0.0     # active_jackpot_stars
        self.jackpot_notify_queue: list[tuple[int, int]] = []  # _jackpot_notify_queue

        # --- golden hour ---
        self.golden_hour_end_dt: Optional[datetime] = None  # golden_hour_end_dt
        self.golden_hour_mult_val: float = 1.5              # golden_hour_mult_val

        # --- bankroll guard ---
        self.bankroll_win_blocked: set[int] = set()  # _bankroll_win_blocked

        # --- auth state (mutable; admins added at runtime) ---
        self.admin_list: set[int] = set()  # admin_list
        self.frozen_users: set[int] = set()  # frozen_users


# Process-wide singleton.
state = State()


class ModuleState:
    """A ``State``-shaped view backed by another module's globals.

    Lets ``core.wallet.Wallet`` operate on the monolith's existing module-level
    globals without migrating the ~158 read/write sites: attribute reads/writes
    are proxied to the backing module's globals via ``field_map`` (state field
    name -> backing global name). Both the monolith's ``global x; x = ...`` and
    the wallet's ``state.x = ...`` therefore hit the very same global, so there
    is a single source of truth and behaviour is unchanged.
    """

    def __init__(self, module, field_map: dict[str, str]) -> None:
        object.__setattr__(self, "_mod", module)
        object.__setattr__(self, "_map", field_map)

    def __getattr__(self, name: str):
        # only called when `name` isn't a real instance attribute (_mod/_map are)
        return getattr(self._mod, self._map.get(name, name))

    def __setattr__(self, name: str, value) -> None:
        setattr(self._mod, self._map.get(name, name), value)
