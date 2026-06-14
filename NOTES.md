# Refactor notes — findings left UNCHANGED

This refactor was deliberately behavior-preserving ("safe structural"): code was
moved verbatim, never rewritten. The items below were noticed during the work but
intentionally **not** changed, so the bot's behavior is identical. They are
recorded here for a future maintainer to decide on.

## 🔐 Security — act on these
- **Rotate the bot token.** The real `BOT_TOKEN` was previously hardcoded in
  `librate_casino.py` and is therefore in git history (a second token also
  appeared in `LAUNCH_GUIDE.md` history). Removing it from source does not purge
  history — generate a new token via @BotFather and update `.env`.
- `.env` (with the live token and `OXAPAY_KEY`) is gitignored — keep it that way.

## 🐛 Pre-existing quirks preserved on purpose
- **Duplicate definitions:** `create_mines_grid_keyboard`,
  `format_mines_game_message`, and `mines_command` are each defined twice in
  `librate_casino.py`; Python keeps the *second* definition. Left as-is.
- **Mojibake string:** `format_withdrawal_status('on_hold')` returns a
  double-encoded "Pending" label (now in `optimus/utils/formatting.py`). Preserved
  byte-for-byte; fix the source bytes only if you intend to change the display.

## 🧹 Housekeeping candidates (not done)
- `optimus.db` and `bot_database.db` are 0 bytes and appear unused (live data is
  in `casino_data.db`). Verify, then remove.
- `ngrok.zip` (~12 MB) is redundant with the extracted `ngrok.exe`.
- `requirements.txt` previously omitted `aiohttp`, `fastapi`, `uvicorn`, and
  `python-dotenv`, which `auto_deposit.py`/`oxapay.py`/`race.py` actually import.
  **Fixed** in this refactor.

## 🏗️ Future decomposition (out of scope for the safe pass)
The high-risk "full decomposition" was intentionally deferred. Good next targets,
each currently entangled with the ~50 shared module globals in `librate_casino.py`:
- `button_callback` (~2,000 lines) and `handle_text_message` (~500 lines) — split
  into per-domain handlers.
- The `t()` translation block (~330 lines) — move to a `translations` module.
- `RANKS`/`LEVELS` constants + `get_user_rank`/`get_rank_info`/`get_user_level`/
  `get_level_progress` — extract to a `leveling` module (left in place here to
  avoid cross-module constant coupling).
- The ~50 module globals — introduce a single `state` container so handlers can be
  moved out of the monolith safely.

## ❓ Roadmap "math.floor" fix — needs a target (NOT applied)
The full-decomposition roadmap grouped a `math.floor` fix under the storage layer,
but `storage.py` stores balances/bets/wins as faithful `float`s with no rounding —
there is no flooring bug to fix there. Applying an unspecified floor to balances
would silently change real user balances, so it was **deliberately not applied**.
If you meant a specific payout/withdrawal/stars-conversion calc (likely in
`librate_casino.py`, not storage), point me at it and I'll apply it precisely.

Applied in Phase 2 (storage): WAL + `synchronous=NORMAL`, indexes on
`game_history(user_id)` and `deposits(status)`, and a run-once `_init_schema`
guard (it previously re-ran all DDL + 3 caught-exception ALTERs on every call).

## ⚠️ Environment
- At refactor time the `C:` drive was **100% full (0 bytes free)**. A casino bot
  writes to SQLite and `backups/` continuously — a full disk will cause failed
  writes / corruption. Free space before running in production.
