# -*- coding: utf-8 -*-
"""Differential equivalence test: core.wallet.Wallet vs the original
librate_casino money functions.

Each scenario is run through BOTH implementations with an identical FakeDB and
identical starting state; we assert the return value, the exact sequence of DB
calls, and the resulting bankroll/jackpot/queue state all match. This proves the
migration changed no economics. No live DB is touched (FakeDB is injected).
"""

from datetime import datetime, timedelta

import core.wallet as wallet_mod
from core.state import State
from core.wallet import Wallet


class FakeDB:
    def __init__(self, admins=()):
        self.calls = []
        self._admins = set(admins)

    def is_admin(self, uid):
        return uid in self._admins

    def get_user_balance(self, uid):
        return 0.0

    def set_user_balance(self, uid, amt):
        self.calls.append(("set_user_balance", uid, amt))

    def adjust_user_balance(self, uid, amt):
        self.calls.append(("adjust_user_balance", uid, amt))

    def set_casino_bankroll(self, amt):
        self.calls.append(("set_casino_bankroll", amt))


FUTURE = datetime.now() + timedelta(hours=1)

# (label, user, amount, game, bankroll, jackpot, golden_end, golden_mult, admin?)
SCENARIOS = [
    ("admin_win_game",      999, 100,    True,  20000.0, 0,   None,   1.5, True),
    ("nonadmin_loss_game",  111, -50,    True,  20000.0, 0,   None,   1.5, False),
    ("nonadmin_win_ok",     111, 100,    True,  20000.0, 0,   None,   1.5, False),
    ("win_blocked",         111, 100,    True,  1.0,     0,   None,   1.5, False),
    ("golden_hour_win",     111, 100,    True,  20000.0, 0,   FUTURE, 1.5, False),
    ("jackpot_win",         111, 100,    True,  20000.0, 500, None,   1.5, False),
    ("jackpot_and_golden",  111, 100,    True,  20000.0, 500, FUTURE, 2.0, False),
    ("nongame_credit",      111, 200,    False, 20000.0, 0,   None,   1.5, False),
    ("loss_drives_floor",   111, -100,   True,  10000.0, 0,   None,   1.5, False),
]


def _run_reference(lc, sc):
    label, user, amount, game, bankroll, jackpot, gend, gmult, is_adm = sc
    fake = FakeDB(admins={user} if is_adm else set())
    lc.db = fake
    lc.wallet.db = fake  # lc.wallet captured the real db at import; patch it for isolation
    lc.casino_bankroll_usd = bankroll
    lc.active_jackpot_stars = jackpot
    lc.golden_hour_end_dt = gend
    lc.golden_hour_mult_val = gmult
    lc._bankroll_win_blocked = set()
    lc._jackpot_notify_queue = []
    lc.STARS_TO_USD = 0.0179
    lc.admin_list = {user} if is_adm else set()
    ret = lc.adjust_user_balance(user, amount, game=game)
    return ret, fake.calls, lc.casino_bankroll_usd, lc.active_jackpot_stars, list(lc._jackpot_notify_queue)


def _run_wallet(sc):
    label, user, amount, game, bankroll, jackpot, gend, gmult, is_adm = sc
    fake = FakeDB(admins=set())
    st = State()
    st.stars_to_usd = 0.0179
    st.casino_bankroll_usd = bankroll
    st.active_jackpot_stars = jackpot
    st.golden_hour_end_dt = gend
    st.golden_hour_mult_val = gmult
    st.admin_list = {user} if is_adm else set()
    w = Wallet(st, fake)
    ret = w.adjust_user_balance(user, amount, game=game)
    return ret, fake.calls, st.casino_bankroll_usd, st.active_jackpot_stars, list(st.jackpot_notify_queue)


def run_all():
    import librate_casino as lc
    failures = []
    for sc in SCENARIOS:
        ref = _run_reference(lc, sc)
        new = _run_wallet(sc)
        if ref != new:
            failures.append((sc[0], ref, new))
    if failures:
        for label, ref, new in failures:
            print(f"  MISMATCH [{label}]\n    ref={ref}\n    new={new}")
        raise SystemExit(f"{len(failures)} scenario(s) diverged")
    print(f"ALL {len(SCENARIOS)} WALLET SCENARIOS MATCH reference exactly")


# pytest entrypoints
def test_wallet_equivalence():
    run_all()


if __name__ == "__main__":
    run_all()
