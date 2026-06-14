# -*- coding: utf-8 -*-
"""
SQLite persistence for casino bot — minimal compatible implementation.
DB file: casino_data.db next to this module.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any

_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_DIR, "casino_data.db")

_DEFAULT_BOT_IDENTITY = {
    "name": "Casino",
    "channel_link": "https://t.me/telegram",
    "chat_link": "https://t.me/telegram",
    "support_username": "telegram",
}


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _json_loads(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


class Database:
    """Thread-safe SQLite facade used by casino v5.py as `db`."""

    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # WAL gives better read/write concurrency; NORMAL sync is the
            # standard safe pairing with WAL. Persistent for the DB file.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        return self._conn

    def get_db_connection(self) -> sqlite3.Connection:
        with self._lock:
            conn = self._connect()
            if not self._schema_ready:
                # Schema is idempotent but expensive; run it once, not on every call.
                self._init_schema(conn)
                self._schema_ready = True
            return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 0,
                crypto_balance REAL NOT NULL DEFAULT 0,
                is_banned INTEGER NOT NULL DEFAULT 0,
                language TEXT,
                last_game_settings TEXT,
                weekly_bonus_claimed TEXT
            );
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT,
                xp INTEGER NOT NULL DEFAULT 0,
                total_games INTEGER NOT NULL DEFAULT 0,
                total_bets REAL NOT NULL DEFAULT 0,
                total_wins REAL NOT NULL DEFAULT 0,
                total_losses REAL NOT NULL DEFAULT 0,
                games_won INTEGER NOT NULL DEFAULT 0,
                games_lost INTEGER NOT NULL DEFAULT 0,
                favorite_game TEXT,
                biggest_win REAL NOT NULL DEFAULT 0,
                game_counts TEXT NOT NULL DEFAULT '{}',
                registration_date TEXT,
                rakeback_balance REAL NOT NULL DEFAULT 0,
                claimed_ranks TEXT NOT NULL DEFAULT '[]',
                last_reload_claim TEXT
            );
            CREATE TABLE IF NOT EXISTS game_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                game_type TEXT,
                bet_amount REAL,
                win_amount REAL,
                won INTEGER NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS username_map (
                username_lower TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referral_codes (
                user_id INTEGER PRIMARY KEY,
                code TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referrers (
                referred_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS referral_stats (
                user_id INTEGER PRIMARY KEY,
                lifetime_earnings REAL NOT NULL DEFAULT 0,
                withdrawable_balance REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                topic TEXT,
                issue TEXT,
                withdrawal_id TEXT,
                status TEXT,
                created TEXT
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS deposits (
                track_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                address TEXT NOT NULL,
                currency TEXT NOT NULL,
                amount_usd REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                pay_amount REAL NOT NULL DEFAULT 0,
                credited INTEGER NOT NULL DEFAULT 0,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pvp_matches (
                match_id TEXT PRIMARY KEY,
                game_type TEXT NOT NULL,
                creator_id INTEGER NOT NULL,
                creator_name TEXT,
                opponent_id INTEGER,
                opponent_name TEXT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER,
                bet REAL NOT NULL,
                multiplier REAL NOT NULL,
                mode TEXT NOT NULL,
                target_score INTEGER NOT NULL,
                creator_score INTEGER NOT NULL DEFAULT 0,
                opponent_score INTEGER NOT NULL DEFAULT 0,
                creator_roll INTEGER,
                opponent_roll INTEGER,
                rolls_this_turn INTEGER NOT NULL DEFAULT 0,
                current_turn INTEGER,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claw_packs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pack_name TEXT UNIQUE,
                title TEXT,
                added_by INTEGER,
                created_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS claw_stickers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pack_id INTEGER,
                file_id TEXT,
                emoji TEXT,
                sticker_position INTEGER,
                FOREIGN KEY(pack_id) REFERENCES claw_packs(id)
            );
            """
        )
        try:
            conn.execute("ALTER TABLE users ADD COLUMN crypto_balance REAL NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column likely exists
        try:
            conn.execute("ALTER TABLE transactions ADD COLUMN track_id TEXT")
        except sqlite3.OperationalError:
            pass # Column already exists
            
        try:
            conn.execute("ALTER TABLE claw_stickers ADD COLUMN sticker_position INTEGER")
        except sqlite3.OperationalError:
            pass # Column already exists
        # Indexes for hot-path lookups (additive; safe to run repeatedly).
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_game_history_user ON game_history(user_id);
            CREATE INDEX IF NOT EXISTS idx_deposits_status   ON deposits(status);
            """
        )
        conn.commit()

    # --- settings ---
    def _get_setting(self, key: str) -> str | None:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return r[0] if r else None

    def _set_setting(self, key: str, value: str | None) -> None:
        with self._lock:
            conn = self.get_db_connection()
            if value is None:
                conn.execute("DELETE FROM settings WHERE key=?", (key,))
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                    (key, value),
                )
            conn.commit()

    def backup_database(self) -> None:
        with self._lock:
            if not os.path.isfile(self.path):
                return
            bdir = os.path.join(_DIR, "backups")
            os.makedirs(bdir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            dst = os.path.join(bdir, f"casino_data_{ts}.db")
            try:
                shutil.copy2(self.path, dst)
            except OSError:
                pass

    # --- counters / globals ---


    def get_ticket_counter(self) -> int:
        v = self._get_setting("ticket_counter")
        return int(v) if v is not None else 1

    def set_ticket_counter(self, n: int) -> None:
        self._set_setting("ticket_counter", str(int(n)))



    def get_casino_bankroll(self) -> float:
        v = self._get_setting("casino_bankroll")
        return float(v) if v is not None else 0.0

    def set_casino_bankroll(self, amount: float) -> None:
        self._set_setting("casino_bankroll", str(float(amount)))



    def get_bot_language(self) -> str:
        return self._get_setting("bot_language") or "en"

    def set_bot_language(self, lang: str) -> None:
        self._set_setting("bot_language", lang)

    def get_gift_comment(self) -> str:
        return self._get_setting("gift_comment") or ""

    def set_gift_comment(self, comment: str) -> None:
        self._set_setting("gift_comment", comment)

    def get_bot_identity(self) -> dict[str, Any]:
        raw = self._get_setting("bot_identity")
        d = _json_loads(raw, {})
        out = dict(_DEFAULT_BOT_IDENTITY)
        out.update(d)
        return out

    def set_bot_identity(self, identity: dict[str, Any]) -> None:
        self._set_setting("bot_identity", _json_dumps(identity))



    def get_frozen_users(self) -> set[int]:
        data = _json_loads(self._get_setting("frozen_users"), [])
        return {int(x) for x in data}

    def set_frozen_users(self, frozen: set[int]) -> None:
        self._set_setting("frozen_users", _json_dumps(sorted(frozen)))

    # --- admins ---
    def is_admin(self, user_id: int) -> bool:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute(
                "SELECT 1 FROM admins WHERE user_id=? LIMIT 1", (user_id,)
            ).fetchone()
            return r is not None

    def get_all_admins(self) -> set[int]:
        with self._lock:
            conn = self.get_db_connection()
            return {int(r[0]) for r in conn.execute("SELECT user_id FROM admins")}

    def add_admin(self, user_id: int) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
            conn.commit()

    def remove_admin(self, user_id: int) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
            conn.commit()

    # --- users / balances ---
    def _ensure_user(self, conn: sqlite3.Connection, user_id: int) -> None:
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))

    def get_user_balance(self, user_id: int) -> float:
        with self._lock:
            conn = self.get_db_connection()
            self._ensure_user(conn, user_id)
            r = conn.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
            return float(r[0] or 0)

    def set_user_balance(self, user_id: int, amount: float) -> None:
        with self._lock:
            conn = self.get_db_connection()
            self._ensure_user(conn, user_id)
            conn.execute(
                "UPDATE users SET balance=? WHERE user_id=?", (float(amount), user_id)
            )
            conn.commit()

    def adjust_user_balance(self, user_id: int, delta: float) -> None:
        with self._lock:
            conn = self.get_db_connection()
            self._ensure_user(conn, user_id)
            conn.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id=?",
                (float(delta), user_id),
            )
            conn.commit()



    def is_user_banned(self, user_id: int) -> bool:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute(
                "SELECT is_banned FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            return bool(r and r[0])

    def set_user_banned(self, user_id: int, banned: bool) -> None:
        with self._lock:
            conn = self.get_db_connection()
            self._ensure_user(conn, user_id)
            conn.execute(
                "UPDATE users SET is_banned=? WHERE user_id=?",
                (1 if banned else 0, user_id),
            )
            conn.commit()

    def set_user_language(self, user_id: int, lang: str) -> None:
        with self._lock:
            conn = self.get_db_connection()
            self._ensure_user(conn, user_id)
            conn.execute("UPDATE users SET language=? WHERE user_id=?", (lang, user_id))
            conn.commit()

    def get_all_user_languages(self) -> dict[int, str]:
        with self._lock:
            conn = self.get_db_connection()
            out: dict[int, str] = {}
            for r in conn.execute(
                "SELECT user_id, language FROM users WHERE language IS NOT NULL"
            ):
                out[int(r[0])] = str(r[1])
            return out

    def set_last_game_settings(self, user_id: int, settings: dict[str, Any]) -> None:
        with self._lock:
            conn = self.get_db_connection()
            self._ensure_user(conn, user_id)
            conn.execute(
                "UPDATE users SET last_game_settings=? WHERE user_id=?",
                (_json_dumps(settings), user_id),
            )
            conn.commit()

    def set_weekly_bonus_claimed(self, user_id: int, when: datetime) -> None:
        with self._lock:
            conn = self.get_db_connection()
            self._ensure_user(conn, user_id)
            conn.execute(
                "UPDATE users SET weekly_bonus_claimed=? WHERE user_id=?",
                (when.isoformat(), user_id),
            )
            conn.commit()

    # --- profiles ---
    def get_or_create_profile(self, user_id: int, username: str | None = None) -> dict[str, Any]:
        with self._lock:
            conn = self.get_db_connection()
            self._ensure_user(conn, user_id)
            row = conn.execute(
                "SELECT * FROM profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            if row is None:
                reg = datetime.now().isoformat()
                conn.execute(
                    """INSERT INTO profiles (user_id, username, display_name, xp, total_games,
                    total_bets, total_wins, total_losses, games_won, games_lost, favorite_game,
                    biggest_win, game_counts, registration_date, rakeback_balance, claimed_ranks, last_reload_claim)
                    VALUES (?,?,?,?,0,0,0,0,0,0,NULL,0,'{}',?,0,'[]',NULL)""",
                    (
                        user_id,
                        (username or "").lstrip("@"),
                        username or "Player",
                        0,
                        reg,
                    ),
                )
                conn.commit()
            elif username:
                conn.execute(
                    "UPDATE profiles SET username=?, display_name=COALESCE(display_name,?) WHERE user_id=?",
                    ((username or "").lstrip("@"), username or "Player", user_id),
                )
                conn.commit()

            row = conn.execute(
                "SELECT * FROM profiles WHERE user_id=?", (user_id,)
            ).fetchone()
            assert row is not None
            gc = _json_loads(row["game_counts"], {})
            reg_raw = row["registration_date"]
            try:
                reg_dt = datetime.fromisoformat(reg_raw) if reg_raw else datetime.now()
            except (TypeError, ValueError):
                reg_dt = datetime.now()
            return {
                "user_id": user_id,
                "username": row["username"] or "",
                "display_name": row["display_name"] or "Player",
                "xp": int(row["xp"] or 0),
                "total_games": int(row["total_games"] or 0),
                "total_bets": float(row["total_bets"] or 0),
                "total_wins": float(row["total_wins"] or 0),
                "total_losses": float(row["total_losses"] or 0),
                "games_won": int(row["games_won"] or 0),
                "games_lost": int(row["games_lost"] or 0),
                "favorite_game": row["favorite_game"],
                "biggest_win": float(row["biggest_win"] or 0),
                "game_counts": gc,
                "registration_date": reg_dt,
                "rakeback_balance": float(row["rakeback_balance"] or 0) if "rakeback_balance" in row.keys() else 0.0,
                "claimed_ranks": _json_loads(row["claimed_ranks"], []) if "claimed_ranks" in row.keys() else [],
                "last_reload_claim": row["last_reload_claim"] if "last_reload_claim" in row.keys() else None,
            }

    def update_profile(
        self,
        user_id: int,
        *,
        total_games: int,
        total_bets: float,
        total_wins: float,
        total_losses: float,
        games_won: int,
        games_lost: int,
        favorite_game: str | None,
        biggest_win: float,
        game_counts: dict[str, Any],
        rakeback_balance: float = 0.0,
        claimed_ranks: list[int] = None,
        last_reload_claim: str | None = None,
    ) -> None:
        if claimed_ranks is None:
            claimed_ranks = []
        with self._lock:
            conn = self.get_db_connection()
            conn.execute(
                """UPDATE profiles SET total_games=?, total_bets=?, total_wins=?, total_losses=?,
                games_won=?, games_lost=?, favorite_game=?, biggest_win=?, game_counts=?,
                rakeback_balance=?, claimed_ranks=?, last_reload_claim=?
                WHERE user_id=?""",
                (
                    total_games,
                    total_bets,
                    total_wins,
                    total_losses,
                    games_won,
                    games_lost,
                    favorite_game,
                    biggest_win,
                    _json_dumps(dict(game_counts)),
                    rakeback_balance,
                    _json_dumps(claimed_ranks),
                    last_reload_claim,
                    user_id,
                ),
            )
            conn.commit()

    def set_username_mapping(self, username: str, user_id: int) -> None:
        with self._lock:
            conn = self.get_db_connection()
            key = username.lower().lstrip("@")
            conn.execute(
                "INSERT OR REPLACE INTO username_map (username_lower, user_id) VALUES (?,?)",
                (key, user_id),
            )
            conn.commit()

    # --- referrals ---
    def get_referral_code(self, user_id: int) -> str | None:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute(
                "SELECT code FROM referral_codes WHERE user_id=?", (user_id,)
            ).fetchone()
            return str(r[0]) if r else None

    def set_referral_code(self, user_id: int, code: str) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute(
                "INSERT OR REPLACE INTO referral_codes (user_id, code) VALUES (?,?)",
                (user_id, code),
            )
            conn.commit()

    def get_referrer(self, referred_user_id: int) -> int | None:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute(
                "SELECT referrer_id FROM referrers WHERE referred_id=?",
                (referred_user_id,),
            ).fetchone()
            return int(r[0]) if r else None

    def set_referrer(self, referred_user_id: int, referrer_id: int) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute(
                "INSERT OR REPLACE INTO referrers (referred_id, referrer_id) VALUES (?,?)",
                (referred_user_id, referrer_id),
            )
            conn.commit()

    def get_referral_stats(self, user_id: int) -> dict[str, float]:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute(
                "SELECT lifetime_earnings, withdrawable_balance FROM referral_stats WHERE user_id=?",
                (user_id,),
            ).fetchone()
            if not r:
                conn.execute(
                    "INSERT OR IGNORE INTO referral_stats (user_id) VALUES (?)",
                    (user_id,),
                )
                conn.commit()
                return {"lifetime_earnings": 0.0, "withdrawable_balance": 0.0}
            return {
                "lifetime_earnings": float(r[0] or 0),
                "withdrawable_balance": float(r[1] or 0),
            }

    def update_referral_stats(
        self, user_id: int, lifetime: float, withdrawable: float
    ) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute(
                """INSERT OR REPLACE INTO referral_stats (user_id, lifetime_earnings, withdrawable_balance)
                VALUES (?,?,?)""",
                (user_id, float(lifetime), float(withdrawable)),
            )
            conn.commit()

    # --- history ---
    def add_game_history(
        self,
        user_id: int,
        game_type: str,
        bet_amount: float,
        win_amount: float,
        won: bool,
    ) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute(
                """INSERT INTO game_history (user_id, game_type, bet_amount, win_amount, won, timestamp)
                VALUES (?,?,?,?,?,?)""",
                (
                    user_id,
                    game_type,
                    float(bet_amount),
                    float(win_amount),
                    1 if won else 0,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()

    def get_game_history(self, user_id: int, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            conn = self.get_db_connection()
            cur = conn.execute(
                """SELECT id, game_type, bet_amount, win_amount, won, timestamp
                FROM game_history WHERE user_id=? ORDER BY id DESC LIMIT ?""",
                (user_id, limit),
            )
            rows = []
            for r in cur.fetchall():
                rows.append(
                    {
                        "id": r["id"],
                        "game_type": r["game_type"],
                        "bet_amount": float(r["bet_amount"] or 0),
                        "win_amount": float(r["win_amount"] or 0),
                        "won": bool(r["won"]),
                        "timestamp": datetime.fromisoformat(r["timestamp"])
                        if r["timestamp"]
                        else datetime.now(),
                    }
                )
            return list(reversed(rows))

    # --- tickets / withdrawals / deposits ---
    def add_ticket(
        self,
        *,
        ticket_id: int,
        user_id: int,
        topic: str | None,
        issue: str | None,
        withdrawal_id: str | None,
        status: str,
        created: datetime,
    ) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute(
                """INSERT OR REPLACE INTO tickets
                (ticket_id, user_id, topic, issue, withdrawal_id, status, created)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    ticket_id,
                    user_id,
                    topic,
                    issue,
                    withdrawal_id,
                    status,
                    created.isoformat() if hasattr(created, "isoformat") else str(created),
                ),
            )
            conn.commit()



    # --- aggregates ---
    def get_top_balances(self, n: int) -> list[tuple[int, float]]:
        with self._lock:
            conn = self.get_db_connection()
            cur = conn.execute(
                "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT ?",
                (int(n),),
            )
            return [(int(r[0]), float(r[1] or 0)) for r in cur.fetchall()]

    def get_total_balance(self) -> float:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone()
            return float(r[0] or 0)
    # --- crypto / auto_deposit logic ---
    def create_deposit(self, user_id: int, track_id: str, address: str, currency: str, amount_usd: float) -> None:
        with self._lock:
            conn = self.get_db_connection()
            created = datetime.utcnow().isoformat()
            conn.execute(
                "INSERT INTO deposits (track_id, user_id, address, currency, amount_usd, status, pay_amount, credited, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (track_id, user_id, address, currency, amount_usd, 'pending', 0.0, 0, created)
            )
            conn.commit()

    def get_pending_deposits(self) -> list[dict]:
        with self._lock:
            conn = self.get_db_connection()
            cur = conn.execute("SELECT track_id, user_id, address, currency, amount_usd, status, pay_amount, credited, created FROM deposits WHERE status='pending'")
            return [dict(r) for r in cur.fetchall()]

    def deposit_already_credited(self, track_id: str) -> bool:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute("SELECT credited FROM deposits WHERE track_id=?", (track_id,)).fetchone()
            if r and r[0] == 1:
                return True
            return False

    def mark_deposit_paid(self, track_id: str, pay_amount: float) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute("UPDATE deposits SET status='paid', pay_amount=?, credited=1 WHERE track_id=?", (pay_amount, track_id))
            conn.commit()

    def mark_deposit_expired(self, track_id: str, status: str) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute("UPDATE deposits SET status=? WHERE track_id=?", (status, track_id))
            conn.commit()

    # --- PvP Methods ---
    def create_pvp_match(self, match_id: str, game_type: str, creator_id: int, creator_name: str, chat_id: int, message_id: int, bet: float, multiplier: float, mode: str, target_score: int):
        with self._lock:
            conn = self._connect()
            now = datetime.utcnow().isoformat()
            conn.execute(
                """
                INSERT INTO pvp_matches 
                (match_id, game_type, creator_id, creator_name, chat_id, message_id, bet, multiplier, mode, target_score, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'waiting', ?, ?)
                """,
                (match_id, game_type, creator_id, creator_name, chat_id, message_id, bet, multiplier, mode, target_score, now, now)
            )
            conn.commit()

    def get_pvp_match(self, match_id: str) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute("SELECT * FROM pvp_matches WHERE match_id = ?", (match_id,)).fetchone()
            return dict(row) if row else None

    def get_active_pvp_match(self, user_id: int, chat_id: int) -> dict | None:
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM pvp_matches WHERE (creator_id = ? OR opponent_id = ?) AND chat_id = ? AND status = 'active'",
                (user_id, user_id, chat_id)
            ).fetchone()
            return dict(row) if row else None

    def update_pvp_match(self, match_id: str, **kwargs):
        with self._lock:
            conn = self._connect()
            kwargs['updated_at'] = datetime.utcnow().isoformat()
            set_clause = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            values = list(kwargs.values()) + [match_id]
            conn.execute(f"UPDATE pvp_matches SET {set_clause} WHERE match_id = ?", values)
            conn.commit()

    def delete_pvp_match(self, match_id: str):
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM pvp_matches WHERE match_id = ?", (match_id,))
            conn.commit()

    def get_timeout_pvp_matches(self, waiting_minutes: int, active_minutes: int) -> list[dict]:
        with self._lock:
            conn = self._connect()
            now = datetime.utcnow()
            # SQLite datetime functions are not always reliable for ISO8601 with python standard library parsing depending on version, 
            # so we'll fetch them all that are waiting or active and check in python.
            rows = conn.execute("SELECT * FROM pvp_matches WHERE status IN ('waiting', 'active')").fetchall()
            
            expired = []
            for row in rows:
                match = dict(row)
                updated_at = datetime.fromisoformat(match['updated_at'])
                diff_minutes = (now - updated_at).total_seconds() / 60.0
                
                if match['status'] == 'waiting' and diff_minutes > waiting_minutes:
                    expired.append(match)
                elif match['status'] == 'active' and diff_minutes > active_minutes:
                    expired.append(match)
            return expired

    def adjust_user_crypto_balance(self, user_id: int, delta: float) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute("UPDATE users SET crypto_balance = crypto_balance + ? WHERE user_id = ?", (delta, user_id))
            conn.commit()

    def get_user_crypto_balance(self, user_id: int) -> float:
        with self._lock:
            conn = self.get_db_connection()
            r = conn.execute("SELECT crypto_balance FROM users WHERE user_id=?", (user_id,)).fetchone()
            return float(r[0]) if r else 0.0


    # --- claw machine ---
    def add_claw_pack(self, pack_name: str, title: str, added_by: int) -> int:
        with self._lock:
            conn = self.get_db_connection()
            cur = conn.execute(
                "INSERT OR IGNORE INTO claw_packs (pack_name, title, added_by, created_at) VALUES (?, ?, ?, ?)",
                (pack_name, title, added_by, datetime.now().isoformat())
            )
            if cur.rowcount == 0:
                # Pack already exists
                cur = conn.execute("SELECT id FROM claw_packs WHERE pack_name = ?", (pack_name,))
            pack_id = cur.lastrowid if cur.lastrowid else cur.fetchone()[0]
            conn.commit()
            return pack_id

    def add_claw_stickers(self, pack_id: int, stickers: list[tuple[str, str, int]]) -> None:
        with self._lock:
            conn = self.get_db_connection()
            # Clear old stickers for this pack if any
            conn.execute("DELETE FROM claw_stickers WHERE pack_id = ?", (pack_id,))
            conn.executemany(
                "INSERT INTO claw_stickers (pack_id, file_id, emoji, sticker_position) VALUES (?, ?, ?, ?)",
                [(pack_id, file_id, emoji, pos) for file_id, emoji, pos in stickers]
            )
            conn.commit()

    def get_all_claw_stickers(self) -> list[tuple[str, int]]:
        with self._lock:
            conn = self.get_db_connection()
            cur = conn.execute("SELECT file_id, sticker_position FROM claw_stickers")
            return [(row["file_id"], row["sticker_position"]) for row in cur.fetchall()]

    def get_claw_packs(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self.get_db_connection()
            cur = conn.execute("SELECT id, pack_name, title, added_by, created_at FROM claw_packs")
            packs = []
            for row in cur.fetchall():
                packs.append({
                    "id": row["id"],
                    "pack_name": row["pack_name"],
                    "title": row["title"],
                    "added_by": row["added_by"],
                    "created_at": row["created_at"]
                })
            return packs

    def delete_claw_pack(self, pack_id: int) -> None:
        with self._lock:
            conn = self.get_db_connection()
            conn.execute("DELETE FROM claw_stickers WHERE pack_id = ?", (pack_id,))
            conn.execute("DELETE FROM claw_packs WHERE id = ?", (pack_id,))
            conn.commit()

db = Database()
