# -*- coding: utf-8 -*-
"""Informational / help commands: /com /cmd /support (+ /help, /support aliases).

Lifted verbatim. Read-only; no module globals rebound. Stable helpers
(t, is_admin, is_private_chat, send_template_message, handle_errors) imported
from librate_casino. Re-imported there so command registration resolves them.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from librate_casino import (
    t,
    logger,
    is_admin,
    is_private_chat,
    send_template_message,
    handle_errors,
)


@handle_errors
async def com_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all available commands for users"""
    if not update.message:
        return
    
    commands_text = t("available_commands")
    
    try:
        await update.message.reply_html(commands_text)
    except Exception as e:
        logger.error(f"Error in com_command: {e}", exc_info=True)
        # Fallback to plain text (remove HTML tags)
        plain_text = commands_text.replace("<b>", "").replace("</b>", "")
        await update.message.reply_text(plain_text)

@handle_errors
async def cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full command reference: user + admin lists (admins only)."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("admin_only", user_id=user_id))
        return

    user_cmds = (
        "📋 <b>All user commands</b>\n\n"
        "<b>Start &amp; help</b>\n"
        "• /start — Welcome &amp; menu\n"
        "• /help — Support / help (alias of /support in this bot)\n"
        "• /support — Tickets &amp; help\n"
        "• /com — Short public command list\n"
        "• /cancel — Cancel current flow\n\n"
        "<b>Balance &amp; money</b>\n"
        "• /balance or /bal — Balance\n"
        "• /deposit or /depo — Deposit Stars\n"
        "• /withdraw — Withdraw Stars (DM)\n"
        "• /custom — Custom deposit amount\n\n"
        "<b>Games &amp; play</b>\n"
        "• /play — Game menu\n"
        "• /mines — Mines game\n"
        "• /dice, /bowl, /dart, /arrow (dart), /football, /basket — Emoji games\n"
        "• /demo — Demo games (no bet)\n"
        "• /predict — Predictor game\n"
        "  /cfad - Setup Coinflip Stickers (Admin)\n"
        "  /cf — Coinflip Game\n"
        "• /blackjack or /bj — Blackjack\n\n"
        "<b>Profile &amp; social</b>\n"
        "• /profile, /levels, /history, /matches, /leaderboard\n"
        "• /bonus, /weekly — Bonuses\n"
        "• /referral or /ref — Referrals\n"
        "• /race or /raffle — Join active race/raffle\n"
        "• /tip — Tip stars\n\n"
        "<b>Other</b>\n"
        "• /hb or /housebal — House bankroll (public view)\n"
        "• /lang — Your language\n"
    )

    admin_cmds = (
        "👑 <b>All admin commands</b>\n\n"
        "<b>Overview</b>\n"
        "• /admin — Compact admin cheat sheet\n"
        "• /cmd — This full list (admin only)\n\n"
        "<b>Admins &amp; users</b>\n"
        "• /addadmin, /removeadmin, /listadmins\n"
        "• /user — User list\n"
        "• /ban, /unban, /freeze, /unfreeze\n\n"
        "<b>Balances</b>\n"
        "• /addbal, /removebal, /setbal, /resetbal, /transferbal\n"
        "• /topbal, /totalbal\n\n"
        "<b>Bot &amp; media</b>\n"
        "• /today — Stats dashboard\n"
        "• /video, /video status, /video remove\n"
        "• /broadcast or /bc — Broadcast to users\n"
        "• /broadcastall — All bots (network)\n"
        "• /demo — Test games without bets\n"
        "• /steal — Rebrand (name, links, support)\n"
        "• /gift — Send gift\n"
        "• /cg — Gift comment\n"
        "• /setlang — Global default language\n"
        "• /set — Crypto deposit addresses\n"
        "• /emoji, /skip — Emoji mapping flow\n"
        "• /wd — Min withdrawal (Stars)\n"
        "• /hb or /housebal — Set casino bankroll (admin mode)\n\n"
        "<b>Events</b>\n"
        "• /rainevent, /jackpot, /doubledeposit, /tripledeposit\n"
        "• /goldenhour, /stopgoldenhour, /cashbackevent, /stopcashback, /eventstatus\n\n"
        "<b>Multi-bot network</b>\n"
        "• /addbot, /removebot, /syncbot, /syncall, /crossban\n"
        "• /sharedblacklist, /botnetwork, /centralstats\n\n"
        "<b>Hidden / misc</b>\n"
        "• /pingme — Admin gift flow helper\n"
    )

    try:
        await update.message.reply_html(user_cmds)
        await update.message.reply_html(admin_cmds)
    except Exception as e:
        logger.error(f"Error in cmd_command: {e}", exc_info=True)

@handle_errors
async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support command - create or view tickets"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Check if command is in group chat
    if not is_private_chat(update):
        keyboard = [
            [InlineKeyboardButton(t("click_here"), url="https://t.me/Iibratebot?start=support")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_html(
            t("please_use_private"),
            reply_markup=reply_markup
        )
        return
    
    # Try to use template first
    template_sent = await send_template_message(
        update.message, context, "help", user_id
    )
    
    if template_sent:
        return
    
    # Fallback to default message
    keyboard = [
        [
            InlineKeyboardButton(t("create_ticket"), callback_data="support_create_ticket"),
            InlineKeyboardButton(t("my_ticket"), callback_data="support_my_tickets")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_html(
        t("support_answers"),
        reply_markup=reply_markup
    )
