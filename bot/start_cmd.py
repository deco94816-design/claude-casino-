# -*- coding: utf-8 -*-
"""/start command: welcome menu + deep-link params (deposit, withdraw, ref code)
and referral attribution.

Lifted verbatim except the global-state bridge: rebound globals (STARS_TO_USD,
bot_identity, referral_code_to_user, user_profiles, user_referrals,
user_referrers, username_to_id) via ``lc.*`` so the in-place referral-dict
mutations hit the live objects. NOTE: ``withdraw_command`` is intentionally left
as a bare (undefined) name -- it is undefined in the original module too, so the
/start withdraw branch raises the same NameError (caught by @handle_errors).
Re-imported into librate_casino so command registration resolves unchanged.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import librate_casino as lc
from librate_casino import (
    db, logger, t, handle_errors, is_admin, is_banned, save_data,
    get_user_balance, get_or_create_profile, detect_lang, register_menu_owner,
    user_languages, deposit_command, support_command,
)


@handle_errors
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Auto-detect user language from Telegram language_code
    user_lang_code = getattr(user, 'language_code', None) or ""

    if user_id not in user_languages:
        detected = detect_lang(user_lang_code)
        user_languages[user_id] = detected
        db.set_user_language(user_id, detected)
        logger.info(f"User {user_id} language detected: {user_lang_code} → {detected}")
    
    # Check if user is banned
    if is_banned(user_id):
        return  # Silently ignore banned users
    
    # Check for start parameters (e.g., /start withdraw, /start deposit, /start ref-CODE)
    if context.args and len(context.args) > 0:
        start_param = context.args[0].lower()
        if start_param == "withdraw":
            # Redirect to withdraw command
            await withdraw_command(update, context)
            return
        elif start_param == "deposit":
            # Redirect to deposit command
            await deposit_command(update, context)
            return
        elif start_param == "support":
            # Redirect to support command
            await support_command(update, context)
            return
        elif start_param.startswith("ref-"):
            # Handle referral code
            try:
                ref_code = start_param.replace("ref-", "").strip()
                if ref_code and ref_code in lc.referral_code_to_user:
                    referrer_id = lc.referral_code_to_user[ref_code]
                    # Only set referrer if user doesn't already have one and isn't referring themselves
                    if user_id not in lc.user_referrers and user_id != referrer_id:
                        lc.user_referrers[user_id] = referrer_id
                        lc.user_referrals[referrer_id].add(user_id)
                        save_data()
                        logger.info(f"User {user_id} joined via referral code {ref_code} from user {referrer_id}")
            except Exception as e:
                logger.error(f"Error processing referral code: {e}", exc_info=True)
    
    get_or_create_profile(user_id, user.username or user.first_name)
    
    # Update username mapping
    if user.username:
        lc.username_to_id[user.username.lower()] = user_id
        save_data()
    
    balance = get_user_balance(user_id)
    balance_usd = balance * lc.STARS_TO_USD
    
    profile = lc.user_profiles.get(user_id, {})
    turnover = profile.get('total_bets', 0.0) * lc.STARS_TO_USD
    
    admin_badge = " 👑" if is_admin(user_id) else ""
    
    # Get bot identity
    bot_name = lc.bot_identity.get("name", "Iibrate")
    channel_link_raw = lc.bot_identity.get("channel_link", "https://t.me/Iibrate")
    chat_link_raw = lc.bot_identity.get("chat_link", "https://t.me/librateds")
    support_username = lc.bot_identity.get("support_username", "Iibratesupport")
    
    # Format channel link (convert @username to https://t.me/username)
    if channel_link_raw.startswith('@'):
        channel_link = f"https://t.me/{channel_link_raw[1:]}"
    elif not channel_link_raw.startswith('http'):
        channel_link = f"https://t.me/{channel_link_raw.replace('@', '')}"
    else:
        channel_link = channel_link_raw
    
    # Format chat link (convert @username to https://t.me/username)
    if chat_link_raw.startswith('@'):
        chat_link = f"https://t.me/{chat_link_raw[1:]}"
    elif not chat_link_raw.startswith('http'):
        chat_link = f"https://t.me/{chat_link_raw.replace('@', '')}"
    else:
        chat_link = chat_link_raw
    
    # Format support link
    if support_username.startswith('@'):
        support_link = f"https://t.me/{support_username[1:]}"
    else:
        support_link = f"https://t.me/{support_username}"
    
    # ── Message 1: Welcome / Getting Started (same template as welcome)
    start_info = t(
        "start_info",
        user_id=user_id,
        bot_name=bot_name,
        admin_badge=admin_badge,
        balance_usd=balance_usd,
        turnover=turnover,
        channel_link=channel_link,
        chat_link=chat_link,
        support_link=support_link,
    )
    await update.message.reply_html(start_info)

    # ── Message 2: Inline Menu ──
    menu_keyboard = [
        [
            InlineKeyboardButton(t("btn_deposit", user_id=user_id), callback_data="balance_deposit"),
            InlineKeyboardButton(t("btn_withdraw", user_id=user_id), callback_data="balance_withdraw"),
        ],
        [
            InlineKeyboardButton(t("btn_balance", user_id=user_id), callback_data="back_to_balance"),
            InlineKeyboardButton(t("btn_stats", user_id=user_id), callback_data="show_profile"),
        ],
        [
            InlineKeyboardButton(t("btn_play", user_id=user_id), callback_data="show_games"),
        ]
    ]
    menu_sent = await update.message.reply_html(
        t("menu_choose", user_id=user_id),
        reply_markup=InlineKeyboardMarkup(menu_keyboard)
    )
    register_menu_owner(menu_sent, user_id)
