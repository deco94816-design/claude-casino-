# -*- coding: utf-8 -*-
"""
race_admin.py — Admin commands for Race management
----------------------------------------------------

COMMANDS:
  /raceprize <amount>              — Set prize pool (e.g. /raceprize 10000)
  /raceend <DD.MM.YYYY HH:MM>     — Set end date   (e.g. /raceend 30.06.2026 12:00)
  /raceboard [page]               — Full leaderboard paginated (20 per page)
  /raceseed add <name> <amount>   — Add a seeded user
  /raceseed edit <rank> <name> <amount> — Edit existing seeded user by rank
  /raceseed list                  — List all seeded users with their rank
  /racereset                      — End current race now, start fresh

INTEGRATION (in librate_casino.py):
  from race_admin import register_race_admin_handlers
  register_race_admin_handlers(app)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes


from race import (
    _get_or_create_active_race,
    _seed_top_users,
    _seed_middle_participants,
    TOP_SEED_USERS,
    TOTAL_PRIZE_USD,
    RACE_DURATION_DAYS,
    RACE_END_HOUR,
    RACE_END_MINUTE,
)

# ─── Admin Guard ─────────────────────────────────────────────────────────────

async def _is_admin(update: Update) -> bool:
    from storage import db
    user_id = update.effective_user.id
    is_adm = db.is_admin(user_id)
    if is_adm:
        return True
    await update.message.reply_text(
        f"⛔ Admin only. DEBUG: your_id={user_id}, is_admin={is_adm}"
    )
    return False


# ─── /raceprize <amount> ─────────────────────────────────────────────────────

async def cmd_race_prize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set prize pool for current active race."""
    from storage import db
    if not await _is_admin(update):
        return

    if not context.args or not context.args[0].replace(".", "").isdigit():
        await update.message.reply_text(
            "Usage: /raceprize <amount>\nExample: /raceprize 10000"
        )
        return

    amount = float(context.args[0])
    if amount <= 0:
        await update.message.reply_text("❌ Amount must be greater than 0.")
        return

    race = _get_or_create_active_race()
    with db._lock:
        conn = db.get_db_connection()
        conn.execute(
            "UPDATE races SET prize_pool=? WHERE race_id=?",
            (amount, race["race_id"]),
        )
        conn.commit()

    await update.message.reply_text(
        f"✅ Prize pool updated!\n\n"
        f"🍀 New prize pool: <b>${amount:,.2f}</b>",
        parse_mode="HTML",
    )


# ─── /raceend <DD.MM.YYYY HH:MM> ─────────────────────────────────────────────

async def cmd_race_end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually set end date of current race."""
    from storage import db
    if not await _is_admin(update):
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /raceend <DD.MM.YYYY> <HH:MM>\n"
            "Example: /raceend 30.06.2026 12:00"
        )
        return

    date_str = f"{context.args[0]} {context.args[1]}"
    try:
        end_dt = datetime.strptime(date_str, "%d.%m.%Y %H:%M").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format. Use: DD.MM.YYYY HH:MM\n"
            "Example: /raceend 30.06.2026 12:00"
        )
        return

    if end_dt <= datetime.now(timezone.utc):
        await update.message.reply_text("❌ End date must be in the future.")
        return

    race = _get_or_create_active_race()
    with db._lock:
        conn = db.get_db_connection()
        conn.execute(
            "UPDATE races SET end_date=? WHERE race_id=?",
            (end_dt.isoformat(), race["race_id"]),
        )
        conn.commit()

    await update.message.reply_text(
        f"✅ End date updated!\n\n"
        f"🕒 New end date: <b>{end_dt.strftime('%d.%m.%Y %H:%M UTC')}</b>",
        parse_mode="HTML",
    )


# ─── /raceboard [page] ───────────────────────────────────────────────────────

async def cmd_race_board(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Full leaderboard — real users only, paginated 20 per page.
    Usage: /raceboard        (page 1)
           /raceboard 3      (page 3)
    """
    from storage import db
    if not await _is_admin(update):
        return

    page = 1
    if context.args and context.args[0].isdigit():
        page = max(1, int(context.args[0]))

    per_page = 20
    offset   = (page - 1) * per_page

    race = _get_or_create_active_race()
    with db._lock:
        conn = db.get_db_connection()

        total = conn.execute(
            "SELECT COUNT(*) FROM race_wagers WHERE race_id=? AND seeded=0",
            (race["race_id"],),
        ).fetchone()[0]

        rows = conn.execute(
            """
            SELECT display_name, wagered_usd,
                   RANK() OVER (ORDER BY wagered_usd DESC) as rank
            FROM race_wagers
            WHERE race_id=?
            ORDER BY wagered_usd DESC
            LIMIT ? OFFSET ?
            """,
            (race["race_id"], per_page, offset),
        ).fetchall()

    total_pages = max(1, -(-total // per_page))  # ceil division

    if not rows:
        await update.message.reply_text("No real users have wagered yet.")
        return

    lines = [
        f"📊 <b>Full Leaderboard</b> — Page {page}/{total_pages}",
        f"Total real participants: {total}\n",
    ]
    for r in rows:
        lines.append(f"#{r[2]} | {r[0]} — ${float(r[1]):.2f}")

    if page < total_pages:
        lines.append(f"\nNext page: /raceboard {page + 1}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ─── /raceseed ───────────────────────────────────────────────────────────────

async def cmd_race_seed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Manage seeded users.
    /raceseed list
    /raceseed add <name> <amount>
    /raceseed edit <rank 1-11> <name> <amount>
    """
    from storage import db
    if not await _is_admin(update):
        return

    if not context.args:
        await update.message.reply_text(
            "Usage:\n"
            "/raceseed list\n"
            "/raceseed add <name> <amount>\n"
            "/raceseed edit <rank 1-11> <name> <amount>"
        )
        return

    sub = context.args[0].lower()

    # ── list ──────────────────────────────────────────────────────────────────
    if sub == "list":
        lines = ["👥 <b>Seeded Users (Top 11)</b>\n"]
        for i, u in enumerate(TOP_SEED_USERS, start=1):
            lines.append(f"#{i} | {u['display_name']} — ${u['wagered_usd']:,.2f}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        return

    # ── add ───────────────────────────────────────────────────────────────────
    if sub == "add":
        # /raceseed add <name> <amount>
        # name can be multiple words, last arg is amount
        if len(context.args) < 3:
            await update.message.reply_text(
                "Usage: /raceseed add <name> <amount>\n"
                "Example: /raceseed add \"King Bet\" 5000"
            )
            return

        try:
            amount = float(context.args[-1])
            name   = " ".join(context.args[1:-1]).strip('"\'')
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return

        if amount <= 0:
            await update.message.reply_text("❌ Amount must be > 0.")
            return

        # Assign next negative ID
        next_id = -(len(TOP_SEED_USERS) + 1)
        TOP_SEED_USERS.append({
            "user_id":      next_id,
            "display_name": name,
            "wagered_usd":  amount,
        })

        # Insert into current race
        race = _get_or_create_active_race()
        with db._lock:
            conn = db.get_db_connection()
            conn.execute(
                "INSERT OR REPLACE INTO race_wagers "
                "(race_id, user_id, display_name, wagered_usd, seeded) "
                "VALUES (?,?,?,?,1)",
                (race["race_id"], next_id, name, amount),
            )
            conn.commit()

        await update.message.reply_text(
            f"✅ Seeded user added!\n\n"
            f"👤 Name: <b>{name}</b>\n"
            f"💵 Wagered: <b>${amount:,.2f}</b>",
            parse_mode="HTML",
        )
        return

    # ── edit ──────────────────────────────────────────────────────────────────
    if sub == "edit":
        # /raceseed edit <rank 1-11> <name> <amount>
        if len(context.args) < 4:
            await update.message.reply_text(
                "Usage: /raceseed edit <rank 1-11> <name> <amount>\n"
                "Example: /raceseed edit 3 NewName 35000"
            )
            return

        if not context.args[1].isdigit():
            await update.message.reply_text("❌ Rank must be a number (1–11).")
            return

        rank = int(context.args[1])
        if rank < 1 or rank > len(TOP_SEED_USERS):
            await update.message.reply_text(
                f"❌ Rank must be between 1 and {len(TOP_SEED_USERS)}."
            )
            return

        try:
            amount = float(context.args[-1])
            name   = " ".join(context.args[2:-1]).strip('"\'')
        except ValueError:
            await update.message.reply_text("❌ Invalid amount.")
            return

        # Update in-memory list
        user_entry = TOP_SEED_USERS[rank - 1]
        old_name   = user_entry["display_name"]
        user_id    = user_entry["user_id"]

        user_entry["display_name"] = name
        user_entry["wagered_usd"]  = amount

        # Update in DB
        race = _get_or_create_active_race()
        with db._lock:
            conn = db.get_db_connection()
            conn.execute(
                "UPDATE race_wagers SET display_name=?, wagered_usd=? "
                "WHERE race_id=? AND user_id=?",
                (name, amount, race["race_id"], user_id),
            )
            conn.commit()

        await update.message.reply_text(
            f"✅ Seeded user updated!\n\n"
            f"Rank: <b>#{rank}</b>\n"
            f"Old name: {old_name}\n"
            f"New name: <b>{name}</b>\n"
            f"New wagered: <b>${amount:,.2f}</b>",
            parse_mode="HTML",
        )
        return

    await update.message.reply_text(
        "Unknown subcommand. Use: list | add | edit"
    )


# ─── /racereset ──────────────────────────────────────────────────────────────

async def cmd_race_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """End current race immediately and start a fresh 30-day race."""
    from storage import db
    if not await _is_admin(update):
        return

    race = _get_or_create_active_race()
    with db._lock:
        conn = db.get_db_connection()
        conn.execute(
            "UPDATE races SET active=0 WHERE race_id=?",
            (race["race_id"],),
        )
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

    end_str = end.strftime("%d.%m.%Y %H:%M UTC")
    await update.message.reply_text(
        f"🔄 Race reset!\n\n"
        f"New race started.\n"
        f"🕒 Ends: <b>{end_str}</b>",
        parse_mode="HTML",
    )


# ─── Register All ─────────────────────────────────────────────────────────────

def register_race_admin_handlers(app) -> None:
    """
    Call once after building PTB Application.
    from race_admin import register_race_admin_handlers
    register_race_admin_handlers(app)
    """
    app.add_handler(CommandHandler("raceprize", cmd_race_prize))
    app.add_handler(CommandHandler("raceend",   cmd_race_end))
    app.add_handler(CommandHandler("raceboard", cmd_race_board))
    app.add_handler(CommandHandler("raceseed",  cmd_race_seed))
    app.add_handler(CommandHandler("racereset", cmd_race_reset))
