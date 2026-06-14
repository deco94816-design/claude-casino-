# -*- coding: utf-8 -*-
"""Multi-bot registry + shared blacklist (local SQLite)."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import time
from typing import Any

import httpx

_DIR = os.path.dirname(os.path.abspath(__file__))
_NETWORK_DB = os.path.join(_DIR, "bot_network.db")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_NETWORK_DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _init() -> None:
    with _conn() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS bots (
                token TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                username TEXT NOT NULL,
                db_path TEXT NOT NULL,
                added_by INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS blacklist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                reason TEXT,
                banned_by INTEGER,
                source_bot TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_bl_user ON blacklist(user_id);
            """
        )
        db.commit()


_init()


class _NetworkDB:
    def is_blacklisted(self, user_id: int) -> bool:
        with _conn() as db:
            r = db.execute(
                "SELECT 1 FROM blacklist WHERE user_id=? LIMIT 1", (user_id,)
            ).fetchone()
            return r is not None

    def get_all_bots(self) -> list[dict[str, Any]]:
        with _conn() as db:
            rows = db.execute(
                "SELECT token, name, username, db_path FROM bots ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_bot_by_token(self, token: str) -> dict[str, Any] | None:
        with _conn() as db:
            r = db.execute("SELECT * FROM bots WHERE token=?", (token,)).fetchone()
            return dict(r) if r else None

    def get_bot_by_name(self, name: str) -> dict[str, Any] | None:
        with _conn() as db:
            r = db.execute(
                "SELECT * FROM bots WHERE name=? COLLATE NOCASE", (name,)
            ).fetchone()
            return dict(r) if r else None

    def add_bot(
        self,
        token: str,
        name: str,
        username: str,
        db_path: str,
        user_id: int,
    ) -> None:
        with _conn() as db:
            db.execute(
                """INSERT OR REPLACE INTO bots (token, name, username, db_path, added_by, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (token, name, username.lstrip("@"), db_path, user_id, time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            db.commit()

    def remove_bot(self, name: str) -> bool:
        with _conn() as db:
            cur = db.execute("DELETE FROM bots WHERE name=? COLLATE NOCASE", (name,))
            db.commit()
            return cur.rowcount > 0

    def add_to_blacklist(
        self,
        target_user_id: int,
        target_username: str | None,
        reason: str | None,
        banned_by: int,
        source_bot: str,
    ) -> None:
        with _conn() as db:
            db.execute(
                """INSERT INTO blacklist (user_id, username, reason, banned_by, source_bot, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    target_user_id,
                    (target_username or "").lstrip("@"),
                    reason or "",
                    banned_by,
                    source_bot,
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                ),
            )
            db.commit()

    def get_blacklist(
        self,
        source_bot: str | None = None,
        reason: str | None = None,
    ) -> list[dict[str, Any]]:
        q = "SELECT user_id, username, reason, source_bot FROM blacklist WHERE 1=1"
        args: list[Any] = []
        if source_bot:
            q += " AND source_bot LIKE ?"
            args.append(f"%{source_bot}%")
        if reason:
            q += " AND reason LIKE ?"
            args.append(f"%{reason}%")
        q += " ORDER BY id DESC"
        with _conn() as db:
            return [dict(r) for r in db.execute(q, args).fetchall()]

    def export_blacklist_csv(
        self,
        source_bot: str | None = None,
        reason: str | None = None,
    ) -> bytes:
        rows = self.get_blacklist(source_bot=source_bot, reason=reason)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["user_id", "username", "reason", "source_bot"])
        for r in rows:
            w.writerow([r.get("user_id"), r.get("username"), r.get("reason"), r.get("source_bot")])
        return buf.getvalue().encode("utf-8")


network_db = _NetworkDB()


def detect_db_path_for_token(token: str) -> list[str]:
    """Heuristic: look for casino_data.db next to known bot dirs (stub returns [])."""
    _ = token
    return []


async def validate_bot_token(token: str) -> tuple[str, str] | None:
    """Call Telegram getMe; return (name, username) or None."""
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url)
            data = r.json()
        if not data.get("ok"):
            return None
        res = data.get("result") or {}
        return (res.get("first_name") or "Bot", (res.get("username") or "bot").lstrip("@"))
    except Exception:
        return None


async def ping_bot(token: str) -> tuple[bool, int | str]:
    """Return (online, latency_ms or 'N/A')."""
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            ok = r.status_code == 200 and r.json().get("ok")
        ms = int((time.perf_counter() - t0) * 1000)
        return bool(ok), ms if ok else "N/A"
    except Exception:
        return False, "N/A"


def sync_settings_to_bot(source_path: str, target_path: str) -> dict[str, Any]:
    """Copy selected settings keys from source casino SQLite to target (same schema)."""
    keys = (
        "min_withdrawal",
        "casino_bankroll",
        "withdraw_video_file_id",
        "bot_language",
        "gift_comment",
        "bot_identity",
        "crypto_addresses",
        "frozen_users",
        "withdrawal_counter",
        "ticket_counter",
    )
    synced: dict[str, Any] = {}
    src = sqlite3.connect(source_path, check_same_thread=False)
    tgt = sqlite3.connect(target_path, check_same_thread=False)
    try:
        tgt.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        for k in keys:
            row = src.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
            if row:
                tgt.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                    (k, row[0]),
                )
                synced[k] = "ok"
        # Admins table
        tgt.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)")
        for row in src.execute("SELECT user_id FROM admins").fetchall():
            tgt.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (row[0],))
        synced["admins"] = "ok"
        tgt.commit()
        # bump sync ts on target
        import random

        ts = str(time.time()) + str(random.randint(0, 9999))
        tgt.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            ("_last_sync_ts", ts),
        )
        tgt.commit()
    finally:
        src.close()
        tgt.close()
    return synced


def crossban_user_on_bot(db_path: str, user_id: int, username: str | None) -> bool:
    """Ensure user row exists and set is_banned=1 on another bot DB."""
    _ = username
    try:
        c = sqlite3.connect(db_path, check_same_thread=False)
        try:
            c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0, crypto_balance REAL DEFAULT 0, is_banned INTEGER DEFAULT 0, language TEXT, last_game_settings TEXT, weekly_bonus_claimed TEXT)")
            c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            c.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (user_id,))
            c.commit()
        finally:
            c.close()
        return True
    except Exception:
        return False


def get_all_user_ids_from_bot(db_path: str) -> list[int]:
    try:
        c = sqlite3.connect(db_path, check_same_thread=False)
        try:
            cur = c.execute("SELECT user_id FROM users")
            return [int(r[0]) for r in cur.fetchall()]
        finally:
            c.close()
    except Exception:
        return []


def get_bot_stats(db_path: str, time_filter: str | None = None) -> dict[str, Any] | None:
    """Aggregate stats from a bot SQLite file."""
    try:
        c = sqlite3.connect(db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
    except Exception:
        return None
    try:
        wh = ""
        if time_filter == "today":
            wh = "WHERE substr(timestamp,1,10)=date('now','localtime')"
        elif time_filter == "week":
            wh = "WHERE timestamp >= datetime('now','-7 days','localtime')"
        elif time_filter == "month":
            wh = "WHERE timestamp >= datetime('now','-30 days','localtime')"

        user_count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        games_row = c.execute(
            f"SELECT COUNT(*), COALESCE(SUM(bet_amount),0), COALESCE(SUM(win_amount),0) FROM game_history {wh}"
        ).fetchone()
        games_count, wagered, payouts = games_row[0], games_row[1], games_row[2]
        profit = (wagered or 0) - (payouts or 0)

        dep = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount_usd),0) FROM deposits WHERE status='paid'"
        ).fetchone()
        wdr = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(stars),0) FROM withdrawals"
        ).fetchone()
        total_balance = c.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone()[0]

        return {
            "user_count": int(user_count or 0),
            "games_count": int(games_count or 0),
            "wagered": float(wagered or 0),
            "payouts": float(payouts or 0),
            "profit": float(profit or 0),
            "deposit_count": int(dep[0] or 0),
            "deposit_total_usd": float(dep[1] or 0),
            "withdrawal_count": int(wdr[0] or 0),
            "withdrawal_total_stars": float(wdr[1] or 0),
            "total_balance": float(total_balance or 0),
        }
    except Exception:
        return None
    finally:
        c.close()
