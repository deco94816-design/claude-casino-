# -*- coding: utf-8 -*-
"""
race.py — Librate Casino Race Module
--------------------------------------
Leaderboard structure:
  Rank #1–11   → Top seeded users (fixed high amounts)
  Rank #12–911 → 900 fake middle participants (fills the gap)
  Rank #912+   → Real users (start here, rise as they wager more)

As real user wagers more they naturally climb past fake middle participants.
"""

from __future__ import annotations

import time
import random
import aiohttp
from datetime import datetime, timedelta, timezone
from storage import db

# ─── Config ──────────────────────────────────────────────────────────────────

TOTAL_PRIZE_USD    = 7000
RACE_DURATION_DAYS = 30
RACE_END_HOUR      = 12
RACE_END_MINUTE    = 0

PRIZES: dict[int, int] = {
    1: 1500,  2: 1000,  3: 900,
    4: 800,   5: 700,   6: 600,
    7: 500,   8: 400,   9: 200,
    10: 200,  11: 200,
}

# ── Top 11 seeded users (ranks 1–11) ─────────────────────────────────────────
# user_id: -1 to -11
TOP_SEED_USERS = [
    {"user_id": -1,  "display_name": "Cold MM",            "wagered_usd": 41519.24},
    {"user_id": -2,  "display_name": "HvGr",               "wagered_usd": 37056.11},
    {"user_id": -3,  "display_name": "Nine MM",            "wagered_usd": 31334.78},
    {"user_id": -4,  "display_name": "Paul Flores Second", "wagered_usd": 30647.26},
    {"user_id": -5,  "display_name": "binned",             "wagered_usd": 26271.71},
    {"user_id": -6,  "display_name": "cK ron",             "wagered_usd": 21136.04},
    {"user_id": -7,  "display_name": "m",                  "wagered_usd": 18426.06},
    {"user_id": -8,  "display_name": "Plug",               "wagered_usd": 15983.53},
    {"user_id": -9,  "display_name": "true osama",         "wagered_usd": 15246.89},
    {"user_id": -10, "display_name": "lumi B4u",           "wagered_usd": 13992.09},
    {"user_id": -11, "display_name": "rin",                "wagered_usd": 12564.96},
]

# ── Middle fake participants (ranks 12–911) ───────────────────────────────────
# user_id: -1000 to -1899
# Amount range: $50 to $12,450 (just below rin's $12,564.96)
FAKE_MID_COUNT = 900

# Name pool for fake participants
_NAMES = [
    "xDrop","ShadowKing","VaultX","CryptoRex","IceRoller","NightCaller",
    "BlitzPro","StormBet","ZenBull","RedAce","DarkLion","SilverFox",
    "QuickFlip","GoldRush","TurboAce","MoonShot","ColdPlay","FastHand",
    "HighRoller","TopGun","WildCard","LuckyDip","SnakeEye","BigBet",
    "StarRush","NeonFox","GhostBet","SteelWolf","IronFist","BlueFire",
    "SwiftAce","RocketMan","CryptoWolf","DiamondX","NightOwl","SilentBet",
    "SpeedKing","TitanBet","PhantomX","LegendPro",
]

# ─── Schema ──────────────────────────────────────────────────────────────────

def init_race() -> None:
    """Call once at bot startup."""
    with db._lock:
        conn = db.get_db_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS races (
                race_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                prize_pool REAL    NOT NULL DEFAULT 7000,
                start_date TEXT    NOT NULL,
                end_date   TEXT    NOT NULL,
                active     INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS race_wagers (
                race_id      INTEGER NOT NULL,
                user_id      INTEGER NOT NULL,
                display_name TEXT    NOT NULL DEFAULT 'Player',
                wagered_usd  REAL    NOT NULL DEFAULT 0.0,
                seeded       INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (race_id, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_race_wagers_board
                ON race_wagers(race_id, wagered_usd DESC);
        """)
        conn.commit()

    race = _get_or_create_active_race()
    _seed_top_users(race["race_id"])
    _seed_middle_participants(race["race_id"])


# ─── Race Lifecycle ───────────────────────────────────────────────────────────

def _get_or_create_active_race() -> dict:
    with db._lock:
        conn = db.get_db_connection()
        r = conn.execute(
            "SELECT race_id, prize_pool, start_date, end_date "
            "FROM races WHERE active=1 ORDER BY race_id DESC LIMIT 1"
        ).fetchone()

        if r:
            return {"race_id": r[0], "prize_pool": r[1],
                    "start_date": r[2], "end_date": r[3]}

        now = datetime.now(timezone.utc)
        end = (now + timedelta(days=RACE_DURATION_DAYS)).replace(
            hour=RACE_END_HOUR, minute=RACE_END_MINUTE, second=0, microsecond=0
        )
        conn.execute(
            "INSERT INTO races (prize_pool, start_date, end_date, active) VALUES (?,?,?,1)",
            (TOTAL_PRIZE_USD, now.isoformat(), end.isoformat()),
        )
        conn.commit()
        r = conn.execute(
            "SELECT race_id, prize_pool, start_date, end_date "
            "FROM races WHERE active=1 ORDER BY race_id DESC LIMIT 1"
        ).fetchone()
        return {"race_id": r[0], "prize_pool": r[1],
                "start_date": r[2], "end_date": r[3]}


def _seed_top_users(race_id: int) -> None:
    """Insert top 11 seeded users (ranks 1–11). Safe to call multiple times."""
    with db._lock:
        conn = db.get_db_connection()
        for u in TOP_SEED_USERS:
            conn.execute(
                "INSERT OR IGNORE INTO race_wagers "
                "(race_id, user_id, display_name, wagered_usd, seeded) "
                "VALUES (?,?,?,?,1)",
                (race_id, u["user_id"], u["display_name"], u["wagered_usd"]),
            )
        conn.commit()


def _seed_middle_participants(race_id: int) -> None:
    """
    Insert 900 fake participants filling ranks 12–911.
    Uses race_id as seed for deterministic but unique-per-race values.
    Safe to call multiple times (INSERT OR IGNORE).
    """
    rng     = random.Random(race_id * 31337)
    max_usd = 12450.00   # just below rin ($12,564.96)
    min_usd = 50.00

    # Generate 900 amounts spread across the range
    amounts = sorted(
        [round(rng.uniform(min_usd, max_usd), 2) for _ in range(FAKE_MID_COUNT)],
        reverse=True,
    )

    with db._lock:
        conn = db.get_db_connection()
        for i, amount in enumerate(amounts):
            fake_id = -(1000 + i)   # -1000 to -1899
            name    = rng.choice(_NAMES) + str(rng.randint(10, 999))
            conn.execute(
                "INSERT OR IGNORE INTO race_wagers "
                "(race_id, user_id, display_name, wagered_usd, seeded) "
                "VALUES (?,?,?,?,1)",
                (race_id, fake_id, name, amount),
            )
        conn.commit()


def _reset_race_if_expired() -> None:
    """End expired race, start fresh 30-day race, re-seed everything."""
    with db._lock:
        conn = db.get_db_connection()
        now     = datetime.now(timezone.utc).isoformat()
        expired = conn.execute(
            "SELECT race_id FROM races WHERE active=1 AND end_date <= ?", (now,)
        ).fetchall()

        if not expired:
            return

        for row in expired:
            conn.execute("UPDATE races SET active=0 WHERE race_id=?", (row[0],))

        start = datetime.now(timezone.utc)
        end   = (start + timedelta(days=RACE_DURATION_DAYS)).replace(
            hour=RACE_END_HOUR, minute=RACE_END_MINUTE, second=0, microsecond=0
        )
        conn.execute(
            "INSERT INTO races (prize_pool, start_date, end_date, active) VALUES (?,?,?,1)",
            (TOTAL_PRIZE_USD, start.isoformat(), end.isoformat()),
        )
        conn.commit()

    new_race = _get_or_create_active_race()
    _seed_top_users(new_race["race_id"])
    _seed_middle_participants(new_race["race_id"])


# ─── Wager Recording ─────────────────────────────────────────────────────────

async def record_wager(user_id: int, display_name: str, bet_stars: int) -> None:
    """
    Call after EVERY bet (win or loss).
    bet_stars = Stars wagered (positive integer).
    user_id must be positive (real Telegram user).
    """
    if bet_stars <= 0 or user_id <= 0:
        return

    ton = await _get_ton_price()
    usd = round(bet_stars * (ton / 200), 4)

    race = _get_or_create_active_race()
    with db._lock:
        conn = db.get_db_connection()
        conn.execute(
            """
            INSERT INTO race_wagers (race_id, user_id, display_name, wagered_usd, seeded)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(race_id, user_id) DO UPDATE SET
                wagered_usd  = wagered_usd + excluded.wagered_usd,
                display_name = excluded.display_name
            """,
            (race["race_id"], user_id, display_name or "Player", usd),
        )
        conn.commit()


# ─── Leaderboard & Rank ───────────────────────────────────────────────────────

def _get_leaderboard(race_id: int, limit: int = 11) -> list[dict]:
    """Top N by wagered_usd — includes seeded + real users."""
    with db._lock:
        conn = db.get_db_connection()
        rows = conn.execute(
            "SELECT user_id, display_name, wagered_usd "
            "FROM race_wagers WHERE race_id=? "
            "ORDER BY wagered_usd DESC LIMIT ?",
            (race_id, limit),
        ).fetchall()

    return [
        {
            "rank":         i + 1,
            "user_id":      row[0],
            "display_name": row[1] or "Player",
            "wagered_usd":  float(row[2]),
        }
        for i, row in enumerate(rows)
    ]


def _get_user_rank(race_id: int, user_id: int) -> tuple[int, float]:
    """
    Returns (rank, wagered_usd).
    rank = 0  → user hasn't placed any bet this race yet.
    Rank counts ALL entries (seeded + real) so real users naturally
    start at ~#912 after placing their first bet.
    """
    with db._lock:
        conn = db.get_db_connection()

        r = conn.execute(
            "SELECT wagered_usd FROM race_wagers WHERE race_id=? AND user_id=?",
            (race_id, user_id),
        ).fetchone()

        if not r:
            return 0, 0.0

        wagered_usd = float(r[0])

        # Count everyone (including fakes) with strictly MORE wagered
        rank_row = conn.execute(
            "SELECT COUNT(*) FROM race_wagers WHERE race_id=? AND wagered_usd > ?",
            (race_id, wagered_usd),
        ).fetchone()

        return int(rank_row[0]) + 1, wagered_usd


# ─── TON Price ───────────────────────────────────────────────────────────────

_ton_cache: dict = {"price": None, "ts": 0.0}


async def _get_ton_price() -> float:
    now = time.monotonic()
    if _ton_cache["price"] and now - _ton_cache["ts"] < 60:
        return _ton_cache["price"]
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=the-open-network&vs_currencies=usd",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                price = float(data["the-open-network"]["usd"])
                _ton_cache["price"] = price
                _ton_cache["ts"]    = now
                return price
    except Exception:
        return _ton_cache["price"] or 3.20


# ─── Message Builder ──────────────────────────────────────────────────────────

async def _build_race_message(user_id: int, display_name: str) -> str:
    race    = _get_or_create_active_race()
    race_id = race["race_id"]

    end_dt = datetime.fromisoformat(race["end_date"])
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    end_str = end_dt.strftime("%d.%m.%Y %H:%M UTC")

    user_rank, user_wagered_usd = _get_user_rank(race_id, user_id)
    leaderboard                 = _get_leaderboard(race_id)

    rank_display = f"#{user_rank}" if user_rank > 0 else "Unranked"

    lines = [
        "🔥 <b>Races</b>",
        "",
        "Join the Librate race! Place bets in mini-games and climb the leaderboard. The higher your rank, the bigger the prize!",
        "",
        f"🍀 <b>${TOTAL_PRIZE_USD:,} race</b>",
        "",
        "#1: $1500",
        "#2: $1000",
        "#3: $900",
        "#4: $800",
        "#5: $700",
        "#6: $600",
        "#7: $500",
        "#8: $400",
        "#9-11: $200",
        "",
        f"👑 Your rank {rank_display}",
        f"<blockquote>📈 Wagered: <b>${user_wagered_usd:.2f}</b></blockquote>",
        f"🕒 End date: {end_str}",
        "",
        "Leaderboard:",
    ]

    if not leaderboard:
        lines.append("No wagers yet. Place a bet to join the race!")
    else:
        for entry in leaderboard:
            lines.append(
                f"<blockquote>#{entry['rank']} | {entry['display_name']} - ${entry['wagered_usd']:.2f}</blockquote>"
            )

    return "\n".join(lines)


# ─── Telegram Handler ─────────────────────────────────────────────────────────

async def race_command(update, context) -> None:
    user = update.effective_user
    name = user.full_name or user.username or "Player"
    msg  = await _build_race_message(user.id, name)
    await update.message.reply_text(msg, parse_mode="HTML")


# ─── Auto Reset Scheduler ─────────────────────────────────────────────────────

def schedule_race_reset(app) -> None:
    """Call once before app.run_polling()."""
    async def _job(context):
        _reset_race_if_expired()

    app.job_queue.run_repeating(
        _job,
        interval=3600,
        first=10,
        name="race_reset_job",
    )
