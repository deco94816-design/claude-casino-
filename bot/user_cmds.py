# -*- coding: utf-8 -*-
"""User-facing core commands: /play (game menu), /balance (+/bal),
/deposit (+/depo), /custom (custom deposit amount).

Lifted verbatim except the bridge for STARS_TO_USD (refreshed at runtime by
/balance), accessed via ``lc.*``. Re-imported into librate_casino so command
registration resolves unchanged.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes

import librate_casino as lc
from librate_casino import (
    PROVIDER_TOKEN, t, translate_text, handle_errors,
    get_user_balance, get_or_create_profile, get_ton_price_usd,
    register_menu_owner, send_bot_reply_html,
)


@handle_errors
async def play_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    get_or_create_profile(user_id, update.effective_user.username or update.effective_user.first_name)
    
    keyboard = [
        [
            InlineKeyboardButton(t("game_dice", user_id=user_id), callback_data="play_game_dice"),
            InlineKeyboardButton(t("game_bowling", user_id=user_id), callback_data="play_game_bowl"),
        ],
        [
            InlineKeyboardButton(t("game_darts", user_id=user_id), callback_data="play_game_dart"),
            InlineKeyboardButton(t("game_football", user_id=user_id), callback_data="play_game_football"),
        ],
        [
            InlineKeyboardButton(t("game_basketball", user_id=user_id), callback_data="play_game_basket"),
            InlineKeyboardButton(t("game_coinflip", user_id=user_id), callback_data="play_game_coinflip"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    play_text = t("play_text", user_id=user_id)
    sent = await send_bot_reply_html(
        update.message, play_text, message_key="play",
        reply_markup=reply_markup, chat_id=update.effective_chat.id
    )
    register_menu_owner(sent, user_id)

@handle_errors
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = get_user_balance(user_id)
    
    ton_price = await get_ton_price_usd()
    if ton_price:
        lc.STARS_TO_USD = ton_price / 200
        
    usd_value = balance * lc.STARS_TO_USD
    
    text = (
        "💰 <b>Your Balance</b>\n\n"
        f"⭐ Stars: <b>{int(balance)}</b> ⭐\n"
        f"💵 USD: <b>${usd_value:.2f}</b>"
    )
    
    await update.message.reply_html(text)

@handle_errors
async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [
            InlineKeyboardButton("10 ⭐", callback_data="deposit_10"),
            InlineKeyboardButton("25 ⭐", callback_data="deposit_25"),
        ],
        [
            InlineKeyboardButton("50 ⭐", callback_data="deposit_50"),
            InlineKeyboardButton("100 ⭐", callback_data="deposit_100"),
        ],
        [
            InlineKeyboardButton("250 ⭐", callback_data="deposit_250"),
            InlineKeyboardButton("500 ⭐", callback_data="deposit_500"),
        ],                [
                    InlineKeyboardButton(t("custom_amount_button", user_id=user_id), callback_data="deposit_custom"),
                ],
        [
            InlineKeyboardButton(t("crypto_deposit_button", user_id=user_id), callback_data="crypto_deposit"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    sent = await send_bot_reply_html(
        update.message, t("select_deposit", user_id=user_id), message_key="deposit",
        reply_markup=reply_markup, chat_id=update.effective_chat.id
    )
    register_menu_owner(sent, update.effective_user.id)

@handle_errors
async def custom_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args or len(context.args) == 0:
        await update.message.reply_html(
            "💳 <b>Custom Deposit</b>\n\n"
            "Usage: /custom <amount>\n"
            "Example: /custom 150\n\n"
            "Minimum: 1 ⭐\n"
            "Maximum: 10000 ⭐"
        )
        return

    try:
        amount = int(context.args[0])

        if amount < 1:
            await update.message.reply_html(translate_text("❌ Minimum deposit is 1 ⭐", user_id=user_id))
            return

        if amount > 10000:
            await update.message.reply_html(translate_text("❌ Maximum deposit is 10000 ⭐", user_id=user_id))
            return
        
        title = f"Deposit {amount} Stars"
        description = f"Add {amount} ⭐ to your game balance"
        payload = f"deposit_{amount}_{update.effective_user.id}"
        prices = [LabeledPrice("Stars", amount)]
        
        await update.message.reply_invoice(
            title=title,
            description=description,
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="XTR",
            prices=prices
        )
    except ValueError:
        await update.message.reply_html(translate_text("❌ Invalid amount! Please enter a number.", user_id=user_id))
