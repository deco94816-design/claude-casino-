# -*- coding: utf-8 -*-
"""Wallet / economics — balances, bankroll, jackpot and golden-hour logic.

Mirrors the original ``librate_casino`` money functions line-for-line
(``is_admin``, ``get_user_balance``, ``adjust_bankroll_usd``,
``bankroll_can_pay``, ``adjust_user_balance``) but operates on a ``State`` object
and a ``storage.Database`` instead of module globals. The monolith delegates to
an instance of this class, so behaviour — including every rounding step and the
bankroll/jackpot/golden-hour edge cases — is identical.
"""

from __future__ import annotations

import logging
from datetime import datetime

from core.state import State

logger = logging.getLogger("librate_casino")  # keep original logger name for parity

ADMIN_BALANCE = 9999999999  # mirrors librate_casino.ADMIN_BALANCE


class Wallet:
    def __init__(self, state: State, db) -> None:
        self.state = state
        self.db = db

    # --- auth (mirrors is_admin) ---
    def is_admin(self, user_id) -> bool:
        return self.db.is_admin(user_id) or user_id in self.state.admin_list

    # --- balances (mirrors get_user_balance / set_user_balance) ---
    def get_user_balance(self, user_id):
        if self.is_admin(user_id):
            return ADMIN_BALANCE
        return self.db.get_user_balance(user_id)

    def set_user_balance(self, user_id, amount) -> None:
        if not self.is_admin(user_id):
            self.db.set_user_balance(user_id, amount)

    # --- bankroll (mirrors adjust_bankroll_usd / bankroll_can_pay) ---
    def adjust_bankroll_usd(self, delta_usd: float) -> None:
        """Update casino bankroll by delta_usd USD, enforcing the floor."""
        s = self.state
        new_val = round(s.casino_bankroll_usd + delta_usd, 2)
        s.casino_bankroll_usd = max(s.bankroll_floor_usd, new_val)
        self.db.set_casino_bankroll(s.casino_bankroll_usd)

    def bankroll_can_pay(self, payout_stars: int) -> bool:
        return self.state.casino_bankroll_usd >= round(payout_stars * self.state.stars_to_usd, 2)

    # --- the economic heart (mirrors adjust_user_balance) ---
    def adjust_user_balance(self, user_id, amount, game: bool = False) -> bool:
        s = self.state
        if not self.is_admin(user_id):
            if game:
                if amount > 0:
                    # Win: check if bankroll can cover the payout
                    payout_usd = round(amount * s.stars_to_usd, 2)
                    if s.casino_bankroll_usd < payout_usd:
                        s.bankroll_win_blocked.add(user_id)
                        logger.warning(
                            f"[BANKROLL] Win BLOCKED user={user_id} "
                            f"payout=${payout_usd:.2f} bankroll=${s.casino_bankroll_usd:.2f}"
                        )
                        return False
                    s.bankroll_win_blocked.discard(user_id)
                    self.adjust_bankroll_usd(-payout_usd)
                else:
                    # Loss: bankroll gains the bet amount
                    self.adjust_bankroll_usd(round(-amount * s.stars_to_usd, 2))
            if amount > 0:
                # Golden hour: boost all game wins
                if s.golden_hour_end_dt and datetime.now() < s.golden_hour_end_dt:
                    amount = int(round(amount * s.golden_hour_mult_val))
                # Jackpot: first game win claims the pot
                if s.active_jackpot_stars > 0:
                    jackpot_won = int(s.active_jackpot_stars)
                    s.active_jackpot_stars = 0
                    s.jackpot_notify_queue.append((user_id, jackpot_won))
                    self.db.adjust_user_balance(user_id, amount + jackpot_won)
                    return True
            self.db.adjust_user_balance(user_id, amount)
        return True
