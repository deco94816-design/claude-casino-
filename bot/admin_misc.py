# -*- coding: utf-8 -*-
"""Admin utilities: /hb /housebal (bankroll) and /broadcast /bc.

Lifted verbatim. casino_bankroll_usd/user_profiles read live via lc.* (rebound
elsewhere); broadcast_waiting + helpers imported. Re-imported so main resolves.
"""

import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import Forbidden

import librate_casino as lc
from librate_casino import broadcast_waiting, db, handle_errors, is_admin, save_data, translate_text


@handle_errors
async def bankroll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show or set bankroll. Admins can set; everyone can view."""
    user_id = update.effective_user.id

    # Admin setting value
    if is_admin(user_id):
        if context.args and len(context.args) >= 1:
            try:
                amount = float(context.args[0])
                lc.casino_bankroll_usd = amount
                save_data()
                await update.message.reply_html(
                    f"✅ Bankroll updated.\n\n🏦 Casino Bankroll\n💵 USD: ${lc.casino_bankroll_usd:,.2f}"
                )
                return
            except ValueError:
                pass  # fall through to prompt
        
        # Prompt admin for amount if not provided or invalid
        context.user_data['waiting_for_bankroll'] = True
        await update.message.reply_html(
            "🏦 <b>Set Casino Bankroll</b>\n\n"
            "Send the bankroll amount in USD (e.g., 2493.23)."
        )
        return
    
    # Non-admins: always read fresh live value from DB
    lc.casino_bankroll_usd = db.get_casino_bankroll()
    await update.message.reply_html(
        f"🏦 <b>Casino Bankroll</b>\n\n"
        f"💵 <b>${lc.casino_bankroll_usd:,.2f}</b>"
    )


@handle_errors





async def perform_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, source_message):
    """Copy the admin's message to all known users who started the bot."""
    admin_id = update.effective_user.id
    total = 0
    sent = 0
    errors = 0
    
    # We consider all known user_ids from profiles as started users
    target_users = list(lc.user_profiles.keys())
    total = len(target_users)
    
    for uid in target_users:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=source_message.chat_id,
                message_id=source_message.message_id
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Forbidden:
            errors += 1
        except Exception:
            errors += 1
    
    await context.bot.send_message(
        chat_id=admin_id,
        text=translate_text(
            f"✅ Broadcast finished.\n"
            f"Total users: {total}\n"
            f"Sent: {sent}\n"
            f"Failed: {errors}",
            user_id=admin_id
        )
    )


@handle_errors
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt admin for a message to broadcast to all users."""
    user_id = update.effective_user.id
    
    # Must be admin
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ Only admins can broadcast.", user_id=user_id))
        return
    
    # Only accept in private chat
    if update.effective_chat.type != "private":
        await update.message.reply_html(translate_text("❌ Use this command in DM with the bot.", user_id=user_id))
        return
    
    broadcast_waiting.add(user_id)
    await update.message.reply_html(
        translate_text(
            "📢 <b>Broadcast Mode</b>\n\n"
            "Send the message you want to broadcast.\n"
            "Supports text, photos, videos, audio (mp3), documents, etc.\n\n"
            "Use /cancel to exit."
        )
    )
