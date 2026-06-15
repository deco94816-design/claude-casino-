# -*- coding: utf-8 -*-
"""Bonus & referral commands: /weekly, /bonus, /referral (and /ref alias).

Lifted verbatim except the global-state bridge: rebound module globals
(STARS_TO_USD, bot_identity, user_referral_balance, user_referral_earnings,
user_referrals) are accessed via ``lc.*``. Re-imported into librate_casino so
command registration resolves unchanged.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import librate_casino as lc
from librate_casino import (
    BOT_USERNAME, t, translate_text, logger, handle_errors,
    calculate_estimated_weekly_bonus, format_time_remaining, get_next_saturday,
    get_or_create_referral_code, get_referral_rate, register_menu_owner,
)


@handle_errors
async def weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    next_saturday = get_next_saturday()
    time_remaining = format_time_remaining(next_saturday)
    estimated_bonus = calculate_estimated_weekly_bonus(user_id)
    
    keyboard = [
        [InlineKeyboardButton(t("redeem_bonus", user_id=user_id), callback_data="redeem_weekly_bonus")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    bot_name = lc.bot_identity.get("name", BOT_USERNAME)
    bonus_text = (
        f"¢° <b>Weekly Bonus Available in {time_remaining}</b>\n\n"
        f"Total estimated Weekly Bonus: {estimated_bonus} ⭐\n\n"
        f"Add @{bot_name} in your name to get your weekly Boosted"
    )
    
    sent = await update.message.reply_html(bonus_text, reply_markup=reply_markup)
    register_menu_owner(sent, user_id)

@handle_errors
async def bonus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    text = "⭐ Receive bonuses for activity and games"
    keyboard = [
        [InlineKeyboardButton("🏆 Rank bonus", callback_data="bonus_rank")],
        [InlineKeyboardButton("🎁 Weekly bonus", callback_data="bonus_weekly")],
        [InlineKeyboardButton("🔄 Rakeback", callback_data="bonus_rakeback")],
        [InlineKeyboardButton("💎 Reload", callback_data="bonus_reload")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        sent = await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        sent = await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")
        register_menu_owner(sent, user_id)

@handle_errors
async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show referral information and link"""
    try:
        user = update.effective_user
        user_id = user.id
        
        # Check if command is in group chat
        if update.effective_chat.type != "private":
            await update.message.reply_html(
                "Please use this command with bot in private messages."
            )
            return
        
        # Get or create referral code
        ref_code = get_or_create_referral_code(user_id)
        
        # Get referral stats
        rate = get_referral_rate(user_id)
        count = len(lc.user_referrals.get(user_id, set()))
        total_earned = lc.user_referral_earnings.get(user_id, 0.0)
        current_balance = lc.user_referral_balance.get(user_id, 0.0)
        
        # Convert to USD
        total_earned_usd = total_earned * lc.STARS_TO_USD
        current_balance_usd = current_balance * lc.STARS_TO_USD
        
        # Get bot username for link
        try:
            bot_info = await context.bot.get_me()
            bot_username = bot_info.username if bot_info.username else "Iibratebot"
        except Exception:
            bot_username = "Iibratebot"  # Fallback
        
        referral_text = (
            f"â¹ï¸  <b>Earn a bonus from the losses of the user you invited</b>\n\n"
            f"🔗 <b>Referral link:</b> t.me/{bot_username}?start=ref-{ref_code}\n"
            f"🔥 <b>Current rate:</b> {rate}%\n"
            f"📈 <b>Users invited:</b> {count}\n"
            f"💵 <b>Total earned:</b> ${total_earned_usd:.2f}\n"
            f"💵 <b>Current referral balance:</b> ${current_balance_usd:.2f}"
        )
        
        await update.message.reply_html(referral_text)
    except Exception as e:
        logger.error(f"Error in referral_command: {e}", exc_info=True)
        await update.message.reply_html(
            translate_text(
                "❌ <b>An error occurred while displaying referral information.</b>\n\n"
                "Please try again later."
            )
        )
