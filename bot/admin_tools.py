# -*- coding: utf-8 -*-
"""Admin tools: /admin (cheat sheet), /today (stats), /user (list),
/steal (rebrand), /emoji (mapping flow), /video (withdrawal video).

Lifted verbatim except the global-state bridge: rebound module globals
(STARS_TO_USD, admin_list, banned_users, emoji_map, user_profiles,
withdraw_video_file_id) are accessed via ``lc.*`` so the monolith and this
module share one source of truth. Re-imported into librate_casino so command
registration resolves unchanged.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import librate_casino as lc
from librate_casino import (
    db, logger, t, translate_text, handle_errors, is_admin,
    get_user_balance, last_bot_messages,
    emoji_replace_flow, extract_emojis_ordered,
)


@handle_errors
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all admin commands"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    
    try:
        total_admins = len(lc.admin_list) if lc.admin_list else 0
    except Exception:
        total_admins = 0
    
    admin_commands_text = (
        "👑 <b>Admin Commands</b>\n\n"
        "<b>Admin Management:</b>\n"
        "• /addadmin [user_id] - Add a new admin\n"
        "• /removeadmin [user_id] - Remove an admin\n"
        "• /listadmins - View all admins\n\n"
        "<b>User Management:</b>\n"
        "• /user - List all users\n"
        "• /ban [user_id/@username] or reply - Ban a user\n"
        "• /unban [user_id/@username] or reply - Unban a user\n"
        "• /freeze [user_id/@username] - Freeze user (no play/deposit/withdraw)\n"
        "• /unfreeze [user_id/@username] - Unfreeze user\n\n"
        "<b>Balance Management:</b>\n"
        "• /addbal [user] [amount] - Add balance\n"
        "• /removebal [user] [amount] - Remove balance\n"
        "• /setbal [user] [amount] - Set exact balance\n"
        "• /resetbal [user] - Reset balance to zero\n"
        "• /transferbal [user1] [user2] [amount] - Transfer balance\n"
        "• /topbal - Top 10 users by balance\n"
        "• /totalbal - Total balance across all users\n\n"
        "<b>Stats:</b>\n"
        "• /today - Dashboard: users, bets, house P/L today vs all time\n\n"
        "<b>Bot Management:</b>\n"
        "• /video - Set withdraw video\n"
        "• /video status - Check video status\n"
        "• /video remove - Remove video\n"
        "• /broadcast or /bc - Send message to all users\n"
        "• /demo - Test games without betting\n"
        "• /steal - Rebrand bot (change name, links, support)\n"
        "• /gift - Send gift to user (emoji or stars)\n"
        "• /cg - Change gift comment\n\n"
        "<b>Bankroll:</b>\n"
        "• /hb or /housebal - Set casino bankroll\n"
        "• /wd - Set minimum withdrawal amount\n\n"
        "<b>Multi-Bot Network:</b>\n"
        "• /addbot [token] - Add bot to network\n"
        "• /removebot [name] - Remove bot from network\n"
        "• /syncbot [token/name] - Sync settings to bot\n"
        "• /syncall - Sync settings to all bots\n"
        "• /crossban [user] - Ban user on all bots\n"
        "• /sharedblacklist - View cross-bot bans\n"
        "• /botnetwork - Network dashboard\n"
        "• /centralstats - Combined stats\n"
        "• /broadcastall - Broadcast to all bots\n\n"
        "<b>Race Management:</b>\n"
        "• /raceprize [amount] - Set prize pool\n"
        "• /raceend [DD.MM.YYYY HH:MM] - Set end date\n"
        "• /raceboard [page] - Full leaderboard\n"
        "• /raceseed list - View seeded users\n"
        "• /raceseed add [name] [amount] - Add seeded user\n"
        "• /raceseed edit [rank] [name] [amount] - Edit seeded user\n"
        "• /racereset - End race now & start fresh\n\n"
        f"<b>Total Admins:</b> {total_admins}\n"
        f"<b>Your Admin ID:</b> <code>{user_id}</code>"
    )
    
    try:
        await update.message.reply_html(admin_commands_text)
    except Exception as e:
        logger.error(f"Error sending admin command message: {e}", exc_info=True)
        # Try sending as plain text if HTML fails
        try:
            plain_text = (
                "👑 Admin Commands\n\n"
                "Admin Management:\n"
                "• /addadmin [user_id] - Add a new admin\n"
                "• /removeadmin [user_id] - Remove an admin\n"
                "• /listadmins - View all admins\n\n"
                "User Management:\n"
                "• /user - List all users\n"
                "• /ban - Ban a user\n"
                "• /unban - Unban a user\n"
                "• /freeze - Freeze user\n"
                "• /unfreeze - Unfreeze user\n\n"
                "Balance Management:\n"
                "• /addbal - Add balance\n"
                "• /removebal - Remove balance\n"
                "• /setbal - Set exact balance\n"
                "• /resetbal - Reset to zero\n"
                "• /transferbal - Transfer between users\n"
                "• /topbal - Top 10 balances\n"
                "• /totalbal - Total all balances\n\n"
                "Bot Management:\n"
                "• /video - Set withdraw video\n"
                "• /broadcast or /bc - Send message to all users\n"
                "• /demo - Test games without betting\n"
                "• /gift - Send gift to user\n"
                "• /cg - Change gift comment\n\n"
                "Bankroll:\n"
                "• /hb or /housebal - Set casino bankroll\n"
                "• /wd - Set minimum withdrawal amount\n\n"
                "Multi-Bot Network:\n"
                "• /addbot - Add bot to network\n"
                "• /removebot - Remove bot\n"
                "• /syncbot - Sync settings to bot\n"
                "• /syncall - Sync to all bots\n"
                "• /crossban - Ban user on all bots\n"
                "• /sharedblacklist - Cross-bot bans\n"
                "• /botnetwork - Network dashboard\n"
                "• /centralstats - Combined stats\n"
                "• /broadcastall - Broadcast to all bots\n\n"
                "Race Management:\n"
                "• /raceprize [amount] - Set prize pool\n"
                "• /raceend [DD.MM.YYYY HH:MM] - Set end date\n"
                "• /raceboard [page] - Full leaderboard\n"
                "• /raceseed list - View seeded users\n"
                "• /raceseed add [name] [amount] - Add seeded user\n"
                "• /raceseed edit [rank] [name] [amount] - Edit seeded user\n"
                "• /racereset - End race now & start fresh\n\n"
                f"Total Admins: {total_admins}\n"
                f"Your Admin ID: {user_id}"
            )
            await update.message.reply_text(plain_text)
        except Exception as e2:
            logger.error(f"Error sending plain text admin command: {e2}", exc_info=True)

@handle_errors
async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: Quick stats dashboard for today vs. all time."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("admin_only_simple", user_id=user_id))
        return

    conn = db.get_db_connection()
    today = datetime.now().strftime('%Y-%m-%d')

    # ── Users ──────────────────────────────────────────────────
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active_today = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM game_history WHERE substr(timestamp,1,10)=?",
        (today,)
    ).fetchone()[0]

    # ── Bets today ─────────────────────────────────────────────
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(bet_amount),0), COALESCE(SUM(win_amount),0) "
        "FROM game_history WHERE substr(timestamp,1,10)=?",
        (today,)
    ).fetchone()
    bets_today, wagered_today, payout_today = row[0], row[1], row[2]
    profit_today = wagered_today - payout_today

    # ── All-time ───────────────────────────────────────────────
    row2 = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(bet_amount),0), COALESCE(SUM(win_amount),0) "
        "FROM game_history"
    ).fetchone()
    bets_all, wagered_all, payout_all = row2[0], row2[1], row2[2]
    profit_all = wagered_all - payout_all

    # ── Top game today ─────────────────────────────────────────
    top_row = conn.execute(
        "SELECT game_type, COUNT(*) AS cnt FROM game_history "
        "WHERE substr(timestamp,1,10)=? GROUP BY game_type ORDER BY cnt DESC LIMIT 1",
        (today,)
    ).fetchone()
    top_game = f"{top_row[0]} ({top_row[1]:,} rounds)" if top_row else "—"

    # ── Stars sitting in wallets ───────────────────────────────
    stars_held = conn.execute("SELECT COALESCE(SUM(balance),0) FROM users").fetchone()[0]

    def s(stars: float) -> str:
        return f"{stars:,.0f} ⭐  (${stars * lc.STARS_TO_USD:,.2f})"

    pl_today_icon = "📈" if profit_today >= 0 else "📉"
    pl_all_icon   = "📈" if profit_all   >= 0 else "📉"

    text = (
        f"📊 <b>Dashboard — {today}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Users</b>\n"
        f"  Registered (all time): <b>{total_users:,}</b>\n"
        f"  Active today: <b>{active_today:,}</b>\n\n"
        f"🎮 <b>Today</b>\n"
        f"  Rounds: <b>{bets_today:,}</b>\n"
        f"  Wagered: <b>{s(wagered_today)}</b>\n"
        f"  Paid out: <b>{s(payout_today)}</b>\n"
        f"  {pl_today_icon} House P/L: <b>{s(profit_today)}</b>\n\n"
        f"📅 <b>All Time</b>\n"
        f"  Rounds: <b>{bets_all:,}</b>\n"
        f"  Wagered: <b>{s(wagered_all)}</b>\n"
        f"  {pl_all_icon} House P/L: <b>{s(profit_all)}</b>\n\n"
        f"💰 Stars in wallets: <b>{s(stars_held)}</b>\n"
        f"🏆 Top game today: <b>{top_game}</b>"
    )
    await update.message.reply_html(text)

@handle_errors
async def set_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to set the withdraw video"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>Admin only command.</b>", user_id=user_id))
        return
    
    # Check if admin wants to view current video status
    if context.args and context.args[0].lower() == 'status':
        if lc.withdraw_video_file_id:
            await update.message.reply_html(
                "🎂¬ <b>Withdraw Video Status</b>\n\n"
                f"✅ Video is set\n"
                f"📎 File ID: <code>{lc.withdraw_video_file_id[:50]}...</code>"
            )
        else:
            await update.message.reply_html(
                "🎂¬ <b>Withdraw Video Status</b>\n\n"
                "❌ No video set yet\n\n"
                "Use /video to set one."
            )
        return
    
    # Check if admin wants to remove video
    if context.args and context.args[0].lower() == 'remove':
        if lc.withdraw_video_file_id:
            lc.withdraw_video_file_id = None
            await update.message.reply_html(
                "✅ <b>Withdraw video removed!</b>\n\n"
                "The /withdraw command will now send text only."
            )
        else:
            await update.message.reply_html(translate_text("❌ No video is currently set.", user_id=user_id))
        return
    
    context.user_data['waiting_for_video'] = True
    await update.message.reply_html(
        "🎂¬ <b>Set Withdraw Video</b>\n\n"
        "Send a video or MP4 file now.\n\n"
        "This video will be sent with every /withdraw command.\n\n"
        "📍 <b>Other options:</b>\n"
        "• /video status - Check current video\n"
        "• /video remove - Remove current video\n"
        "• /cancel - Cancel this operation"
    )

@handle_errors
async def steal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to rebrand the bot"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    
    # Initialize steal flow
    context.user_data['steal_state'] = 'active'
    context.user_data['steal_new_name'] = None
    context.user_data['steal_channel_link'] = None
    context.user_data['steal_chat_link'] = None
    context.user_data['steal_support_username'] = None
    context.user_data['steal_channel_yes'] = False
    context.user_data['steal_chat_yes'] = False
    context.user_data['steal_support_yes'] = False
    
    keyboard = [
        [
            InlineKeyboardButton(translate_text("✅ Yes", user_id=user_id), callback_data="steal_name_yes"),
            InlineKeyboardButton(translate_text("❌ No", user_id=user_id), callback_data="steal_name_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(
        "🎂­ <b>Bot Rebranding</b>\n\n"
        "This will change the bot's identity:\n"
        "• Bot name (replaces 'Iibrate' everywhere)\n"
        "• Channel link\n"
        "• Chat link\n"
        "• Support username\n\n"
        "📍 <b>Do you want to change the bot name?</b>",
        reply_markup=reply_markup
    )

@handle_errors
async def emoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: Start custom emoji replacement flow for the last bot message in this chat."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not is_admin(user_id):
        await update.message.reply_html(t("emoji_admin_only", user_id=user_id))
        return

    # Get last bot message for this chat
    last = last_bot_messages.get(chat_id)
    if not last:
        await update.message.reply_html(
            "❌ <b>No tracked bot message found in this chat.</b>\n\n"
            "Send a command first (e.g. /start, /balance), then use /emoji to customise its emojis."
        )
        return

    text = last["text"]
    all_emojis = extract_emojis_ordered(text)
    if not all_emojis:
        await update.message.reply_html(t("emoji_no_emojis", user_id=user_id))
        return

    # Only ask for emojis NOT already in global map (no re-asking)
    emojis_to_ask = [(em, pos) for em, pos in all_emojis if em not in lc.emoji_map]
    if not emojis_to_ask:
        await update.message.reply_html(
            "✅ <b>All emojis in the last message are already mapped.</b> No new custom emojis to set."
        )
        return

    emoji_replace_flow[user_id] = {
        "chat_id": chat_id,
        "emojis": emojis_to_ask,
        "current_index": 0,
        "total": len(emojis_to_ask),
    }

    preview_lines = [f"  {i + 1}. {em}" for i, (em, _) in enumerate(emojis_to_ask)]
    preview = "\n".join(preview_lines)
    first_emoji = emojis_to_ask[0][0]

    await update.message.reply_html(
        f"🎯 <b>Global Emoji</b>\n\n"
        f"<b>{len(emojis_to_ask)}</b> emoji(s) not yet saved:\n{preview}\n\n"
        f"¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â¢â\n"
        f"Send a <b>custom emoji</b> to replace #1 ({first_emoji}). /skip to keep · /cancel to abort."
    )

@handle_errors
async def user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all users (admin only)"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ You are not authorized"))
        return
    
    try:
        # Get all users from profiles
        all_user_ids = list(lc.user_profiles.keys())
        
        if not all_user_ids:
            await update.message.reply_html(translate_text("📋 <b>User List</b>\n\nNo users found."))
            return
        
        # Sort by user ID
        all_user_ids.sort()
        
        # Check if pagination is needed (Telegram message limit is 4096 characters)
        total_users = len(all_user_ids)
        
        # Build user list
        user_list_text = f"📋 <b>User List</b>\n\n"
        user_list_text += f"Total users: <b>{total_users}</b>\n\n"
        
        # List users (limit to avoid message too long)
        max_users_per_message = 50
        users_to_show = all_user_ids[:max_users_per_message]
        
        for idx, uid in enumerate(users_to_show, 1):
            profile = lc.user_profiles.get(uid, {})
            username = profile.get('username', '')
            display_name = profile.get('display_name', '')
            balance = get_user_balance(uid)
            
            # Format username display
            if username:
                user_display = f"@{username}"
            elif display_name:
                user_display = display_name
            else:
                user_display = f"User {uid}"
            
            # Check if banned
            banned_status = "🔨" if uid in lc.banned_users else ""
            
            user_list_text += f"{idx}. <code>{uid}</code> - {user_display} {banned_status}\n"
        
        if total_users > max_users_per_message:
            user_list_text += f"\n... and {total_users - max_users_per_message} more users"
        
        await update.message.reply_html(user_list_text)
        
        logger.info(f"Admin {user_id} viewed user list ({total_users} users)")
        
    except Exception as e:
        logger.error(f"Error in user_command: {e}", exc_info=True)
        await update.message.reply_html(
            "❌ <b>An error occurred while displaying user list.</b>\n\n"
            "Please try again later."
        )
