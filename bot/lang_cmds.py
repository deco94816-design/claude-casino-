# -*- coding: utf-8 -*-
"""Language commands: /lang (user preference) and /setlang (admin global default).

Lifted verbatim except the global-state bridge: ``bot_language`` (rebound by
load_data and setlang) and the ``user_languages`` map are accessed via ``lc.*``
so the monolith and this module share one source of truth. Re-imported back into
librate_casino so command registration resolves unchanged.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import librate_casino as lc
from librate_casino import t, db, logger, is_admin, handle_errors


@handle_errors
async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change user language preference"""
    if not update.message:
        return

    user_id = update.effective_user.id
    current_lang = lc.user_languages.get(user_id, "en")

    lang_options = [
        ("🇬🇧 English", "en"),
        ("🇷🇺 Ð ÑÑÑÐºÐ¸Ð¹", "ru"),
        ("🇩🇪 Deutsch", "de"),
        ("🇫🇷 Français", "fr"),
        ("🇨🇳 中文", "zh"),
    ]

    # Build buttons — 2 per row, checkmark on current
    keyboard = []
    row = []
    for label, code in lang_options:
        mark = " ✓" if code == current_lang else ""
        row.append(InlineKeyboardButton(f"{label}{mark}", callback_data=f"set_lang_{code}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    lang_names = {"en": "English", "ru": "Ð ÑÑÑÐºÐ¸Ð¹", "de": "Deutsch", "fr": "Français", "zh": "中文"}
    current_name = lang_names.get(current_lang, "English")

    await update.message.reply_html(
        f"🌐 <b>Language Selection</b>\n\n"
        f"Current language: <b>{current_name}</b>\n\n"
        f"Select your preferred language:",
        reply_markup=reply_markup
    )

@handle_errors
async def setlang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change bot language (admin only - for global default)"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(t("admin_only", user_id=user_id))
        return
    
    
    # Toggle language
    if lc.bot_language == "en":
        lc.bot_language = "ru"
        message = t("language_changed_ru", user_id=user_id)
    else:
        lc.bot_language = "en"
        message = t("language_changed_en", user_id=user_id)
    
    db.set_bot_language(lc.bot_language)
    await update.message.reply_html(message)
    logger.info(f"Admin {user_id} changed bot language to {lc.bot_language}")
