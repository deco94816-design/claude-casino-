# -*- coding: utf-8 -*-
"""Multi-bot network admin commands (/addbot /removebot /syncbot /syncall
/crossban /sharedblacklist /botnetwork /centralstats /broadcastall) + the
check_sync_reload job. Lifted verbatim from librate_casino; network primitives
imported from bot_network, shared helpers from librate_casino. Re-imported into
librate_casino so main's handlers/job resolve them.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from bot_network import (
    network_db, validate_bot_token, ping_bot, detect_db_path_for_token,
    sync_settings_to_bot, crossban_user_on_bot, get_bot_stats, get_all_user_ids_from_bot,
)
from librate_casino import (
    is_admin, is_banned, db, t, translate_text, handle_errors, logger, BOT_TOKEN,
)


@handle_errors
async def addbot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register a new bot in the network."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    if not context.args or len(context.args) < 1:
        await update.message.reply_html(
            "📡 <b>Add Bot to Network</b>\n\n"
            "Usage: /addbot [bot_token]\n"
            "Optional: /addbot [bot_token] [db_path]"
        )
        return

    token = context.args[0].strip()
    explicit_db_path = context.args[1].strip() if len(context.args) > 1 else None

    # Check if already registered
    existing = network_db.get_bot_by_token(token)
    if existing:
        await update.message.reply_html(
            f"⚠️ Bot <b>@{existing['username']}</b> is already registered in the network."
        )
        return

    # Validate token via Telegram API
    msg = await update.message.reply_html(t("validating_token", user_id=user_id))
    result = await validate_bot_token(token)
    if not result:
        await msg.edit_text("❌ Invalid or expired bot token.")
        return

    name, username = result

    # Determine db_path
    if explicit_db_path:
        db_path = explicit_db_path
    else:
        candidates = detect_db_path_for_token(token)
        # Filter out our own DB
        own_path = os.path.abspath(db.path)
        candidates = [c for c in candidates if os.path.abspath(c) != own_path]

        if len(candidates) == 1:
            db_path = candidates[0]
        elif len(candidates) > 1:
            listing = "\n".join(f"  {i+1}. <code>{c}</code>" for i, c in enumerate(candidates))
            await msg.edit_text(
                f"⚠️ Multiple databases found. Re-run with explicit path:\n"
                f"<code>/addbot {token} [path]</code>\n\nCandidates:\n{listing}",
                parse_mode=ParseMode.HTML
            )
            return
        else:
            await msg.edit_text(
                f"⚠️ Could not auto-detect database path.\n"
                f"Re-run: <code>/addbot {token} /full/path/to/bot_data.db</code>",
                parse_mode=ParseMode.HTML
            )
            return

    if not os.path.exists(db_path):
        await msg.edit_text(f"❌ Database file not found:\n<code>{db_path}</code>", parse_mode=ParseMode.HTML)
        return

    network_db.add_bot(token, name, username, db_path, user_id)
    total_bots = len(network_db.get_all_bots())

    await msg.edit_text(
        f"✅ <b>Bot registered successfully!</b>\n\n"
        f"👤 Name: <b>{name}</b>\n"
        f"📛 Username: @{username}\n"
        f"💾 DB: <code>{db_path}</code>\n"
        f"🌐 Network size: <b>{total_bots}</b> bot(s)",
        parse_mode=ParseMode.HTML
    )


@handle_errors
async def removebot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a bot from the network."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    if not context.args:
        bots = network_db.get_all_bots()
        if not bots:
            await update.message.reply_html(t("no_bots_network", user_id=user_id))
            return
        listing = "\n".join(f"  • <b>{b['name']}</b> (@{b['username']})" for b in bots)
        await update.message.reply_html(
            f"📡 <b>Remove Bot</b>\n\n"
            f"Usage: /removebot [bot_name]\n\n"
            f"Registered bots:\n{listing}"
        )
        return

    name = " ".join(context.args).strip()
    if network_db.remove_bot(name):
        await update.message.reply_html(f"✅ Bot '<b>{name}</b>' removed from network.")
    else:
        await update.message.reply_html(f"❌ Bot '<b>{name}</b>' not found in network.")


@handle_errors
async def syncbot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sync settings from this bot to a target bot (with confirmation)."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    if not context.args:
        await update.message.reply_html(
            "🔄 <b>Sync Bot Settings</b>\n\n"
            "Usage: /syncbot [token_or_name]\n\n"
            "Syncs: admins, crypto addresses, game settings, "
            "min withdrawal, bot identity, language, gift comment."
        )
        return

    target = context.args[0].strip()
    bot_info = network_db.get_bot_by_name(target)
    if not bot_info:
        bot_info = network_db.get_bot_by_token(target)
    if not bot_info:
        await update.message.reply_html(t("err_bot_not_in_network", user_id=user_id))
        return

    context.user_data["sync_target_bot"] = bot_info

    keyboard = [
        [
            InlineKeyboardButton(t("btn_confirm_sync", user_id=user_id), callback_data="network_sync_confirm"),
            InlineKeyboardButton(t("btn_cancel", user_id=user_id), callback_data="network_sync_cancel")
        ]
    ]
    await update.message.reply_html(
        f"🔄 <b>Sync Preview</b>\n\n"
        f"Target: <b>{bot_info['name']}</b> (@{bot_info['username']})\n"
        f"DB: <code>{bot_info['db_path']}</code>\n\n"
        f"<b>Will sync:</b>\n"
        f"  • Admin list\n"
        f"  • Crypto addresses\n"
        f"  • Min withdrawal\n"
        f"  • Bot identity\n"
        f"  • Bot language\n"
        f"  • Gift comment\n"
        f"  • Casino bankroll\n"
        f"  • Frozen users\n\n"
        f"⚠️ This will <b>overwrite</b> settings on the target bot.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@handle_errors
async def syncall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sync settings from this bot to ALL bots in the network."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    bots = network_db.get_all_bots()
    if not bots:
        await update.message.reply_html(t("no_bots_registered", user_id=user_id))
        return

    msg = await update.message.reply_html(t("syncing_settings", user_id=user_id))
    source_path = os.path.abspath(db.path)
    results = []
    for bot_info in bots:
        if os.path.abspath(bot_info["db_path"]) == source_path:
            results.append(f"  ⏭ {bot_info['name']}: Skipped (self)")
            continue
        try:
            synced = sync_settings_to_bot(source_path, bot_info["db_path"])
            results.append(f"  ✅ {bot_info['name']}: OK ({len(synced)} items)")
        except Exception as e:
            results.append(f"  ❌ {bot_info['name']}: FAILED ({e})")

    report = "\n".join(results)
    await msg.edit_text(
        f"🔄 <b>Sync All Results</b>\n\n{report}",
        parse_mode=ParseMode.HTML
    )


@handle_errors
async def crossban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban a user across all bots in the network."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    target_user_id = None
    target_username = None
    reason = ""

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = update.message.reply_to_message.from_user.id
        target_username = update.message.reply_to_message.from_user.username
        reason = " ".join(context.args) if context.args else ""
    elif context.args:
        try:
            target_user_id = int(context.args[0])
        except ValueError:
            arg = context.args[0].lstrip("@")
            found_id = username_to_id.get(arg.lower())
            if found_id:
                target_user_id = found_id
                target_username = arg
            else:
                await update.message.reply_html(t("err_user_not_found_plain", user_id=user_id))
                return
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    else:
        await update.message.reply_html(
            "🚫 <b>Cross-Ban User</b>\n\n"
            "Usage: /crossban [user_id] [reason]\n"
            "Or reply to a user's message with /crossban [reason]"
        )
        return

    if is_admin(target_user_id):
        await update.message.reply_html(t("cannot_crossban_admin", user_id=user_id))
        return

    msg = await update.message.reply_html(t("crossbanning_user", user_id=user_id))

    # 1. Add to shared blacklist
    this_bot_name = bot_identity.get("name", "Unknown")
    network_db.add_to_blacklist(target_user_id, target_username, reason, user_id, this_bot_name)

    # 2. Ban on this bot
    banned_users.add(target_user_id)
    conn = db.get_db_connection()
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (target_user_id,))
    conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (target_user_id,))
    conn.commit()

    # 3. Ban on all network bots
    bots = network_db.get_all_bots()
    source_path = os.path.abspath(db.path)
    ban_results = []
    for bot_info in bots:
        if os.path.abspath(bot_info["db_path"]) == source_path:
            ban_results.append(f"  ✅ {bot_info['name']}: OK (this bot)")
            continue
        success = crossban_user_on_bot(bot_info["db_path"], target_user_id, target_username)
        ban_results.append(
            f"  {'✅' if success else '❌'} {bot_info['name']}: {'OK' if success else 'FAILED'}"
        )

    # 4. Notify admins on all bots
    notify_text = (
        f"🚫 <b>CROSSBAN</b>\n\n"
        f"User: <code>{target_user_id}</code>"
        + (f" (@{target_username})" if target_username else "")
        + (f"\nReason: {reason}" if reason else "")
        + f"\nBanned by: <code>{user_id}</code>"
        + f"\nSource: {this_bot_name}"
    )
    admin_ids = db.get_all_admins() | admin_list
    for bot_info in bots:
        try:
            notify_bot = Bot(token=bot_info["token"])
            for aid in admin_ids:
                if aid == user_id:
                    continue  # Skip the admin who ran the command
                try:
                    await notify_bot.send_message(
                        chat_id=aid, text=notify_text, parse_mode=ParseMode.HTML
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.05)
        except Exception:
            pass

    report = "\n".join(ban_results)
    await msg.edit_text(
        f"🚫 <b>Crossban Complete</b>\n\n"
        f"User: <code>{target_user_id}</code>"
        + (f" (@{target_username})" if target_username else "")
        + (f"\nReason: {reason}" if reason else "")
        + f"\n\n<b>Results:</b>\n{report}",
        parse_mode=ParseMode.HTML
    )


@handle_errors
async def sharedblacklist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the combined shared blacklist across all bots."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    source_bot_filter = None
    reason_filter = None
    export_csv = False
    if context.args:
        for arg in context.args:
            if arg.lower() == "csv":
                export_csv = True
            elif arg.startswith("bot:"):
                source_bot_filter = arg[4:]
            elif arg.startswith("reason:"):
                reason_filter = arg[7:]

    if export_csv:
        csv_bytes = network_db.export_blacklist_csv(
            source_bot=source_bot_filter, reason=reason_filter
        )
        bio = io.BytesIO(csv_bytes)
        bio.name = "shared_blacklist.csv"
        await update.message.reply_document(document=bio, caption="📋 Shared Blacklist Export")
        return

    entries = network_db.get_blacklist(source_bot=source_bot_filter, reason=reason_filter)
    if not entries:
        await update.message.reply_html(t("shared_blacklist_empty", user_id=user_id))
        return

    lines = []
    for e in entries[:50]:
        line = f"  • <code>{e['user_id']}</code>"
        if e.get('username'):
            line += f" @{e['username']}"
        if e.get('reason'):
            line += f" — {e['reason']}"
        line += f" [{e.get('source_bot', '?')}]"
        lines.append(line)

    text = f"📋 <b>Shared Blacklist</b> ({len(entries)} total)\n\n"
    if len(entries) > 50:
        text += "<i>(Showing first 50. Use <code>csv</code> to export all.)</i>\n\n"
    text += "\n".join(lines)
    text += "\n\n<i>Filters: /sharedblacklist [bot:name] [reason:text] [csv]</i>"

    await update.message.reply_html(text)


@handle_errors
async def botnetwork_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dashboard showing all bots, online/offline, stats, ping."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    bots = network_db.get_all_bots()
    if not bots:
        await update.message.reply_html(t("no_bots_registered", user_id=user_id))
        return

    msg = await update.message.reply_html(t("gathering_status", user_id=user_id))

    lines = []
    total_users = 0
    total_games = 0
    total_revenue = 0.0

    for bot_info in bots:
        online, latency = await ping_bot(bot_info["token"])
        status_icon = "🟢 ONLINE" if online else "🔴 OFFLINE"
        ping_str = f"{latency}ms" if online else "N/A"

        stats = get_bot_stats(bot_info["db_path"], time_filter="today")
        if stats:
            users = stats["user_count"]
            games = stats["games_count"]
            revenue = stats["profit"]
            total_users += users
            total_games += games
            total_revenue += revenue
        else:
            users = games = 0
            revenue = 0.0

        lines.append(
            f"<b>{bot_info['name']}</b> (@{bot_info['username']})\n"
            f"  {status_icon} | Ping: {ping_str}\n"
            f"  👥 Users: {users:,} | 🎮 Games today: {games:,}\n"
            f"  💰 Revenue today: {revenue:,.0f} ⭐ (${revenue * STARS_TO_USD:,.2f})"
        )

    text = (
        f"🌐 <b>Bot Network Dashboard</b>\n"
        f"{'━' * 28}\n\n"
        + "\n\n".join(lines)
        + f"\n\n{'━' * 28}\n"
        f"<b>TOTALS:</b> {total_users:,} users | "
        f"{total_games:,} games | "
        f"{total_revenue:,.0f} ⭐ (${total_revenue * STARS_TO_USD:,.2f}) revenue"
    )
    await msg.edit_text(text, parse_mode=ParseMode.HTML)


@handle_errors
async def centralstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Combined stats across all bots with time filter and export."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    time_filter = None
    export = False
    if context.args:
        for arg in context.args:
            if arg.lower() in ("today", "week", "month"):
                time_filter = arg.lower()
            elif arg.lower() == "export":
                export = True

    bots = network_db.get_all_bots()
    if not bots:
        await update.message.reply_html(t("no_bots_registered", user_id=user_id))
        return

    msg = await update.message.reply_html(t("gathering_stats", user_id=user_id))

    all_stats = []
    totals = {
        "user_count": 0, "games_count": 0, "wagered": 0, "payouts": 0,
        "profit": 0, "deposit_count": 0, "deposit_total_usd": 0,
        "withdrawal_count": 0, "withdrawal_total_stars": 0, "total_balance": 0
    }

    for bot_info in bots:
        stats = get_bot_stats(bot_info["db_path"], time_filter=time_filter)
        if stats:
            stats["bot_name"] = bot_info["name"]
            all_stats.append(stats)
            for key in totals:
                totals[key] += stats.get(key, 0)

    filter_label = time_filter.upper() if time_filter else "ALL TIME"

    if export:
        lines = [f"Central Stats Report — {filter_label}\n"]
        lines.append(f"Generated: {datetime.now().isoformat()}\n\n")
        for s in all_stats:
            lines.append(f"--- {s['bot_name']} ---\n")
            for k, v in s.items():
                if k != "bot_name":
                    lines.append(f"  {k}: {v}\n")
            lines.append("\n")
        lines.append(f"--- TOTALS ---\n")
        for k, v in totals.items():
            lines.append(f"  {k}: {v}\n")
        bio = io.BytesIO("".join(lines).encode("utf-8"))
        bio.name = f"central_stats_{filter_label.lower()}.txt"
        await update.message.reply_document(document=bio, caption=f"📊 Central Stats: {filter_label}")
        return

    def s(stars):
        return f"{stars:,.0f} ⭐ (${stars * STARS_TO_USD:,.2f})"

    per_bot_lines = []
    for st in all_stats:
        per_bot_lines.append(
            f"  • <b>{st['bot_name']}</b>: {st['user_count']:,} users | "
            f"{st['games_count']:,} games | P/L: {s(st['profit'])}"
        )

    text = (
        f"📊 <b>Central Stats — {filter_label}</b>\n\n"
        f"👥 Users: <b>{totals['user_count']:,}</b>\n"
        f"🎮 Games: <b>{totals['games_count']:,}</b>\n"
        f"💵 Wagered: {s(totals['wagered'])}\n"
        f"💸 Paid out: {s(totals['payouts'])}\n"
        f"🏠 House P/L: {s(totals['profit'])}\n"
        f"📥 Deposits: {totals['deposit_count']:,} (${totals['deposit_total_usd']:,.2f})\n"
        f"📤 Withdrawals: {totals['withdrawal_count']:,} ({s(totals['withdrawal_total_stars'])})\n"
        f"💰 Balances held: {s(totals['total_balance'])}\n\n"
        f"<b>Per Bot:</b>\n" + "\n".join(per_bot_lines)
        + "\n\n<i>Usage: /centralstats [today|week|month] [export]</i>"
    )
    await msg.edit_text(text, parse_mode=ParseMode.HTML)


@handle_errors
async def broadcastall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a message to ALL users across ALL bots."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized", user_id=user_id))
        return

    if update.effective_chat.type != "private":
        await update.message.reply_html(t("use_dm_bot", user_id=user_id))
        return

    bots = network_db.get_all_bots()
    if not bots:
        await update.message.reply_html(t("no_bots_registered", user_id=user_id))
        return

    context.user_data["broadcastall_waiting"] = True
    bot_listing = "\n".join(f"  • <b>{b['name']}</b> (@{b['username']})" for b in bots)
    await update.message.reply_html(
        f"📢 <b>Broadcast All Mode</b>\n\n"
        f"Target bots:\n{bot_listing}\n\n"
        f"Send the message you want to broadcast.\n"
        f"Use /cancel to exit."
    )


# ── Multi-bot sync reload ────────────────────────────────────────────────────
_last_known_sync_ts = None

async def check_sync_reload(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job: detect if settings were synced by another bot and reload."""
    global _last_known_sync_ts
    try:
        current_ts = db._get_setting("_last_sync_ts")
        if current_ts and current_ts != _last_known_sync_ts:
            _last_known_sync_ts = current_ts
            logger.info(f"[SYNC] Detected new sync timestamp: {current_ts}. Reloading settings...")
            load_data()
            logger.info("[SYNC] Settings reloaded successfully.")
    except Exception as e:
        logger.error(f"[SYNC] Reload check failed: {e}")
