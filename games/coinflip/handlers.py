# -*- coding: utf-8 -*-
"""Coinflip handlers: /cf, /cfad admin setup, sticker capture, menu, cancel.

Lifted verbatim from librate_casino. Inline cf_* game-resolution callbacks remain
in button_callback for now (they call get_cf_menu etc. via the re-imported names).
Shared non-rebound state imported from librate_casino; STARS_TO_USD read live.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import (
    t, handle_errors, is_banned, is_admin, get_user_balance,
    get_or_create_profile, get_user_link, game_sessions,
    coinflip_sessions, cflip_setup, coinflip_stickers, CF_MULTIPLIER,
)
from games.coinflip.stickers import load_coinflip_stickers, save_coinflip_stickers


@handle_errors
async def cflip_setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    cflip_setup[user_id] = {"step": "heads"}
    await update.message.reply_html(t("cf_setup_send_heads", user_id=user_id))

@handle_errors
async def handle_cflip_sticker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in cflip_setup:
        return

    sticker = update.message.sticker
    if not sticker:
        return

    step = cflip_setup[user_id]["step"]

    if step == "heads":
        coinflip_stickers["heads"] = sticker.file_id
        cflip_setup[user_id]["step"] = "tails"
        await update.message.reply_html(t("cf_setup_heads_saved", user_id=user_id))
    elif step == "tails":
        coinflip_stickers["tails"] = sticker.file_id
        save_coinflip_stickers()
        del cflip_setup[user_id]
        await update.message.reply_html(t("cf_setup_complete", user_id=user_id))

def get_cf_menu(user_id, balance_stars, use_stars=False):
    b_10 = int(balance_stars * 0.10)
    b_25 = int(balance_stars * 0.25)
    b_50 = int(balance_stars * 0.50)
    b_100 = int(balance_stars)

    if b_10 < 1: b_10 = 1
    if b_25 < 1: b_25 = 1
    if b_50 < 1: b_50 = 1
    if b_100 < 1: b_100 = 1

    if use_stars:
        btn_10 = f"{b_10} ⭐"
        btn_25 = f"{b_25} ⭐"
        btn_50 = f"{b_50} ⭐"
        btn_100 = f"{b_100} ⭐"
        balance_str = f"{balance_stars:,} ⭐"
        toggle_btn = "💵 USD"
    else:
        btn_10 = f"${(b_10 * lc.STARS_TO_USD):.2f}"
        btn_25 = f"${(b_25 * lc.STARS_TO_USD):.2f}"
        btn_50 = f"${(b_50 * lc.STARS_TO_USD):.2f}"
        btn_100 = f"${(b_100 * lc.STARS_TO_USD):.2f}"
        balance_str = f"${(balance_stars * lc.STARS_TO_USD):.2f}"
        toggle_btn = "🪙 Coins"

    text = (
        '<blockquote>The game "coin flip" is a simple game of choosing between two options: head or tails. The player places a bet and guesses the outcome — if correct, they receive winnings equal to 2x their stake; if incorrect, the stake is lost. Everything is decided by a single click and a bit of luck.</blockquote>\n'
        '⬆️ Choose a bet or enter your own\n'
        'Minimum bet - 0.10\n\n'
        f'🔵 Current balance: {balance_str}'
    )

    keyboard = [
        [
            InlineKeyboardButton(btn_10, callback_data=f"cf_bet_btn_{b_10}"),
            InlineKeyboardButton(btn_25, callback_data=f"cf_bet_btn_{b_25}")
        ],
        [
            InlineKeyboardButton(btn_50, callback_data=f"cf_bet_btn_{b_50}"),
            InlineKeyboardButton(btn_100, callback_data=f"cf_bet_btn_{b_100}")
        ],
        [InlineKeyboardButton(toggle_btn, callback_data="cf_toggle_curr")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

async def cf_cancel_game(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, user_id: int, amount_usd: float):
    # This will be called when game expires (Phase 2)
    profile = get_or_create_profile(user_id)
    display_name = profile.get('display_name') or profile.get('username') or 'Player'
    user_link = get_user_link(user_id, display_name)
    
    text = f"🌑 Coin Flip game by {user_link} for ${amount_usd:.2f} was canceled — nobody accepted the invitation"
    
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass
        
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

@handle_errors
async def cf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or ''

    if is_banned(user_id):
        return

    get_or_create_profile(user_id, username)

    if user_id in coinflip_sessions:
        await update.message.reply_html(t("cf_active", user_id=user_id))
        return

    if user_id in game_sessions:
        await update.message.reply_html(t("finish_current_game", user_id=user_id))
        return

    balance = get_user_balance(user_id)

    # Check args for custom bet
    args = context.args
    if args:
        try:
            bet_amount = int(args[0])
            if bet_amount <= 0:
                await update.message.reply_html(t("bet_greater_than_zero", user_id=user_id))
                return
            if balance < bet_amount:
                await update.message.reply_html(f"❌ Insufficient balance!\n💵 Your balance: <b>{balance:,} ⭐</b>")
                return
            
            await update.message.delete()
            context.user_data['cf_bet'] = bet_amount
            bet_usd = bet_amount * lc.STARS_TO_USD
            profile = get_or_create_profile(user_id)
            display_name = profile.get('display_name') or profile.get('username') or 'Player'
            user_link = get_user_link(user_id, display_name)
            
            text = (
                f"🌑 Coin Flip game by {user_link}\n\n"
                f"Bet: ${bet_usd:.2f}\n"
                f"Multiplier: ×{CF_MULTIPLIER}"
            )
            
            keyboard = [
                [InlineKeyboardButton("🤖  Play against bot", callback_data="cf_play_bot")],
                [InlineKeyboardButton("🔴  Cancel game", callback_data="cf_cancel_challenge")]
            ]
            
            sent_msg = await context.bot.send_message(
                chat_id=update.message.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            
            context.job_queue.run_once(
                cf_challenge_timeout, 
                60, 
                data={
                    'chat_id': update.message.chat_id, 
                    'message_id': sent_msg.message_id,
                    'user_id': user_id,
                    'bet_stars': bet_amount
                },
                name=f"cf_timeout_{sent_msg.message_id}"
            )
            return
        except ValueError:
            pass

    # No args or invalid arg, show the menu
    use_stars = context.user_data.get('cf_use_stars', False)
    text, markup = get_cf_menu(user_id, balance, use_stars)
    sent = await update.message.reply_html(text, reply_markup=markup)
    register_menu_owner(sent, user_id)
