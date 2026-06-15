# -*- coding: utf-8 -*-
"""Translation lookup: t() (key-based UI strings) and translate_text()
(runtime English->target string translation).

Translation DATA lives in languages.py (LANG_STRINGS / get_lang_string); this
module holds only the lookup logic lifted verbatim from the monolith. The
per-user language map (user_languages) is read via ``lc.*`` (stable, mutated in
place) so there is one source of truth. Re-imported into librate_casino so the
many ``from librate_casino import t, translate_text`` call sites resolve unchanged.
"""

import re

from languages import get_lang_string

import librate_casino as lc


def t(key, **kwargs):
    """Translation function - returns text based on current bot language"""
    translations = {
        "en": {
            # Welcome & Main
            "welcome": "👑 <b>Welcome to {bot_name} Game{admin_badge}</b>\n\n⭐ {bot_name} Game is the best online mini-games on Telegram\n\n📢 <b>How to start winning?</b>\n\n1. Make sure you have a balance. You can top up using the \"Deposit\" button.\n\n2. Join one of our groups from the {bot_name} catalog.\n\n3. Type /play and start playing!\n\n\n💵 Balance: ${balance_usd:.2f}\n👑 Game turnover: ${turnover:.2f}\n\n🌐 <b>About us</b>\n<a href='{channel_link}'>Channel</a> | <a href='{chat_link}'>Chat</a> | <a href='{support_link}'>Support</a>",
            "play_button": "🎮 Play",
            "balance": "Balance",
            "deposit": "Deposit",
            "withdraw": "Withdraw",
            "profile": "Profile",
            "help": "Help",
            "support": "Support",
            
            # Language
            "language_changed_en": "✅ <b>Language changed to English!</b>\n\nThe bot is now using English language.",
            "language_changed_ru": "language_changed_ru",
            
            # Common
            "admin_only": "❌ <b>You don't have permission to use this command.</b>",
            "support_answers": "Support answers in 1—5 minutes.",
            "create_ticket": "✅ Create ticket",
            "my_ticket": "🗒 my ticket",
            "please_use_private": "Please use this command with bot in private messages.",
            "click_here": "Click here",
            
            # Help
            "help_text": "help_text",
            "admin_commands": "👑 <b>Admin Commands:</b>\n/addadmin - Add new admin\n/removeadmin - Remove admin\n/listadmins - View all admins\n/demo - Test games without betting\n/video - Set withdraw video\n/video status - Check video status\n/video remove - Remove video\n/broadcast or /bc - Send a message to all users\n",
            
            # Commands list
            "available_commands": "📋 <b>Available Commands</b>\n\n<b>Basic Commands:</b>\n• /start - Start the bot\n• /help - Show help information\n• /cancel - Cancel current operation\n\n<b>Balance & Money:</b>\n• /balance or /bal - Check your balance\n• /deposit or /depo - Deposit stars\n• /withdraw - Withdraw stars to TON wallet\n\n<b>Games:</b>\n• /play - Start playing games\n\n<b>Profile & Stats:</b>\n• /profile - View your profile\n• /levels - View your level and progress\n• /history - View your game history\n• /leaderboard - View top players\n\n<b>Rewards:</b>\n• /weekly - Claim weekly bonus (Saturdays only)\n• /referral or /ref - View referral information\n\n<b>Social:</b>\n• /tip [amount] - Send stars to another user\n\n<b>Support:</b>\n• /support - Get help or create a support ticket\n\n💡 <b>Tip:</b> Use /help for more information about any command.",
            
            # Balance
            "your_balance": "💰 <b>Your Balance</b>{admin_note}\n\n⭐ Stars: <b>{balance:,} ⭐</b>\n💵 USD: <b>${balance_usd:.2f}</b>",
            "deposit_button": "💳 Deposit",
            "withdraw_button": "💎 Withdraw",
            
            # Deposit
            "select_deposit": "💳 <b>Select deposit amount:</b>",
            "custom_amount": "💳 Custom Amount",
            
            # Withdraw
            "private_command_only": "🔒 <b>Private Command Only</b>\n\nFor your security, the /withdraw command can only be used in a private chat with the bot.\n\n👉 <a href='https://t.me/{bot_username}?start=withdraw'>Click here to open DM</a>\n\nOr search for @{bot_username} and start a private conversation.",
            "welcome_withdraw": "welcome_withdraw",
            "withdraw_button_text": "💎 Withdraw",

            # Main menu / inline (missing keys)
            "menu_choose": "👇 Choose an option:",
            "btn_deposit": "💳 Deposit",
            "btn_withdraw": "💎 Withdraw",
            "btn_balance": "💰 Balance",
            "btn_stats": "📊 Stats",
            "btn_play": "🎮 Play",
            "btn_deposit_inline": "💳 Deposit",
            "btn_withdraw_inline": "💎 Withdraw",
            "back_button": "🔙 Back",
            "back_to_games": "🎮 Back to Games",
            "game_dice": "🎲 Dice",
            "game_bowling": "🎳 Bowling",
            "game_bowl": "🎳 Bowling",
            "game_darts": "🎯 Darts",
            "game_dart": "🎯 Darts",
            "game_football": "⚽ Football",
            "game_basketball": "🏀 Basketball",
            "game_coinflip": "🪙 Coinflip",
            "demo_dice_btn": "🎲 Dice",
            "demo_bowl_btn": "🎳 Bowling",
            "demo_dart_btn": "🎯 Darts",
            "demo_football_btn": "⚽ Football",
            "demo_basketball_btn": "🏀 Basketball",
            "cancel_demo": "❌ Cancel Demo",
            "btn_cancel_demo": "❌ Cancel Demo",
            "mode_normal": "Normal",
            "mode_double": "Double",
            "mode_crazy": "Crazy",
            "cancel_game": "🗑 Cancel",
            "btn_cancel_game": "🗑 Cancel",
            "btn_cancel_game2": "🗑 Cancel",
            "play_again": "🔄 Play Again",
            "btn_play_again": "🔄 Play Again",
            "btn_up_to_1": "First to 1 point",
            "btn_up_to_2": "First to 2 points",
            "btn_up_to_3": "First to 3 points",
            "btn_confirm": "✅ Confirm",
            "btn_cancel": "❌ Cancel",
            "btn_flip_coin": "🪙 Flip!",
            "cancel_button": "❌ Cancel",
            "bj_custom_btn": "✏️ Custom Bet",
            "btn_custom_bet": "✏️ Custom Bet",
            "btn_change_bet": "✏️ Change Bet",
            "pred_active": "⚡ Active Game",
            "btn_all_in": "💰 All In",
            "custom_amount_button": "✏️ Custom Amount",
            "crypto_deposit_button": "💎 Crypto Deposit",
            "withdraw_stars_button": "⭐ Withdraw Stars",
            "withdraw_crypto_button": "💎 Withdraw Crypto",
            "refresh_button": "🔄 Refresh",
            "btn_open_payment": "💳 Open Payment",
            "btn_pay_now": "💳 Pay Now",
            "crypto_bitcoin": "₿ Bitcoin",
            "crypto_ethereum": "Ξ Ethereum",
            "crypto_litecoin": "Ł Litecoin",
            "crypto_solana": "◎ Solana",
            "crypto_ton": "💎 TON",
            "crypto_usdt_bep20": "💵 USDT (BEP20)",
            "crypto_usdc_erc20": "💵 USDC (ERC20)",
            "crypto_monero": "🔒 Monero",
            "oxapay_usdt": "💵 USDT",
            "oxapay_btc": "₿ BTC",
            "oxapay_eth": "Ξ ETH",
            "oxapay_ltc": "Ł LTC",
            "oxapay_doge": "🐕 DOGE",
            "btn_yes": "✅ Yes",
            "btn_no": "❌ No",
            "btn_stars_dep": "⭐ Stars",
            "btn_crypto_dep": "💎 Crypto",
            "btn_confirm_sync": "✅ Confirm Sync",
            "redeem_bonus": "🎂 Redeem Bonus",
            "claim_bonus_locked": "🔒 Bonus Locked",
        },
        "ru": {
            # Welcome & Main
            "welcome": "💎 <b>¢â¬¾±â¢â¬¾ ¿¾¶°»¾²°â¢â¬Å¡ââ ² {bot_name} ¡°·¸½¾{admin_badge}</b>\n\nâ­ {bot_name} - »âââ¢â¬¡ââ ¸µ ¼¸½¸-¸³â¢â¬â¢â¬¹ ² Telegram\n\n📢 <b>¡°º ½°â¢â¬¡°â¢â¬Å¡ââ ²â¢â¬¹¸³â¢â¬â¢â¬¹²°â¢â¬Å¡ââ?</b>\n\n1. £±µ´¸â¢â¬Å¡µâââ, â¢â¬¡â¢â¬Å¡¾ у ²°â µââ¢â¬Å¡ââ ±°»°½â. ¢â¬â¢â¢â¬¹ ¼¾¶µâ¢â¬Å¡µ ¿¾¿¾»½¸â¢â¬Å¡ââ ±°»°½â, ¸â¿¾»ââ·âââ º½¾¿ºââ \"¸¾¿¾»½¸â¢â¬Å¡ââ\".\n\n2. ¸â¢â¬¸â¾µ´¸½â¹â¢â¬Å¡µâââ º ½°ââ ¸¼ ³â¢â¬ââ¿¿°¼ ¸· º°â¢â¬Å¡°»¾³° {bot_name}.\n\n3. ¢â¬â¢²µ´¸â¢â¬Å¡µ /play ¸ ½°â¢â¬¡½¸â¢â¬Å¡µ ¸³â¢â¬°â¢â¬Å¡ââ!\n\n\n💵 ¢â¬Ë°»°½â: ${balance_usd:.2f}\n👑 ¾±¾â¢â¬¾â¢â¬Å¡ ¸³â¢â¬: ${turnover:.2f}\n\nð <b>¾ ½°â</b>\n<a href='{channel_link}'>¡°½°»</a> | <a href='{chat_link}'>§°â¢â¬Å¡</a> | <a href='{support_link}'>¸¾´´µâ¢â¬¶º°</a>",
            "play_button": "play_button",
            "balance": "balance",
            "deposit": "deposit",
            "withdraw": "withdraw",
            "profile": "profile",
            "help": "help",
            "support": "support",
            
            # Language
            "language_changed_en": "✅ <b>Language changed to English!</b>\n\nThe bot is now using English language.",
            "language_changed_ru": "language_changed_ru",
            
            # Common
            "admin_only": "admin_only",
            "support_answers": "support_answers",
            "create_ticket": "create_ticket",
            "my_ticket": "my_ticket",
            "please_use_private": "please_use_private",
            "click_here": "click_here",
            
            # Help
            "help_text": "help_text",
            "admin_commands": "admin_commands",
            
            # Commands list
            "available_commands": "available_commands",
            
            # Balance
            "your_balance": "your_balance",
            "deposit_button": "deposit_button",
            "withdraw_button": "withdraw_button",
            
            # Deposit
            "select_deposit": "select_deposit",
            "custom_amount": "custom_amount",
            
            # Withdraw
            "private_command_only": "private_command_only",
            "welcome_withdraw": "welcome_withdraw",
            "withdraw_button_text": "withdraw_button_text",

            # Main menu / inline (missing keys) — UTF-8; latin-1 decode in t() is a no-op for these
            "menu_choose": "👇 Выберите вариант:",
            "btn_deposit": "💳 Пополнить",
            "btn_stats": "📊 Ð¡Ñ‚Ð°Ñ‚Ð¸ÑÑ‚Ð¸ÐºÐ°",
            "btn_play": "🎮 Играть",
            "btn_deposit_inline": "💳 Пополнить",
            "btn_withdraw_inline": "💎 Ð’Ñ‹Ð²ÐµÑÑ‚Ð¸",
            "back_button": "🔙 ÐÐ°Ð·Ð°Ð´",
            "back_to_games": "🎮 К играм",
            "game_dice": "🎲 ÐšÐ¾ÑÑ‚Ð¸",
            "game_bowling": "🎳 Боулинг",
            "game_bowl": "🎳 Боулинг",
            "game_darts": "🎯 Ð”Ð°Ñ€Ñ‚Ñ",
            "game_dart": "🎯 Ð”Ð°Ñ€Ñ‚Ñ",
            "game_football": "⚽ Футбол",
            "game_basketball": "🏀 Баскетбол",
            "game_coinflip": "🪙 Монетка",
            "demo_dice_btn": "🎲 ÐšÐ¾ÑÑ‚Ð¸",
            "demo_bowl_btn": "🎳 Боулинг",
            "demo_dart_btn": "🎯 Ð”Ð°Ñ€Ñ‚Ñ",
            "demo_football_btn": "⚽ Футбол",
            "demo_basketball_btn": "🏀 Баскетбол",
            "cancel_demo": "âŒ Отменить демо",
            "btn_cancel_demo": "âŒ Отменить демо",
            "mode_normal": "Обычный",
            "mode_double": "Двойной",
            "mode_crazy": "Безумный",
            "cancel_game": "🗑 Отмена",
            "btn_cancel_game": "🗑 Отмена",
            "btn_cancel_game2": "🗑 Отмена",
            "play_again": "🔄 Ещё раз",
            "btn_play_again": "🔄 Ещё раз",
            "btn_up_to_1": "First to 1 point",
            "btn_up_to_2": "First to 2 points",
            "btn_up_to_3": "First to 3 points",
            "btn_confirm": "✅ Подтвердить",
            "btn_cancel": "âŒ Отмена",
            "btn_flip_coin": "btn_flip_coin",
            "cancel_button": "âŒ Отмена",
            "bj_custom_btn": "✏️ Своя ставка",
            "btn_custom_bet": "✏️ Своя ставка",
            "btn_change_bet": "btn_change_bet",
            "pred_active": "⚡ Игра идёт",
            "btn_all_in": "💰 Ва-банк",
            "custom_amount_button": "custom_amount_button",
            "crypto_deposit_button": "💎 Крипто-пополнение",
            "withdraw_stars_button": "â­ Вывод Stars",
            "withdraw_crypto_button": "💎 Вывод крипты",
            "refresh_button": "🔄 Обновить",
            "btn_open_payment": "💳 Открыть оплату",
            "btn_pay_now": "💳 Оплатить",
            "crypto_bitcoin": "₿ Bitcoin",
            "crypto_ethereum": "Ξ Ethereum",
            "crypto_litecoin": "Ł Litecoin",
            "crypto_solana": "◎ Solana",
            "crypto_ton": "💎 TON",
            "crypto_usdt_bep20": "💵 USDT (BEP20)",
            "crypto_usdc_erc20": "💵 USDC (ERC20)",
            "crypto_monero": "🔒 Monero",
            "oxapay_usdt": "💵 USDT",
            "oxapay_btc": "₿ BTC",
            "oxapay_eth": "Ξ ETH",
            "oxapay_ltc": "Ł LTC",
            "oxapay_doge": "🐕 DOGE",
            "btn_yes": "✅ Да",
            "btn_no": "❌ Нет",
            "btn_stars_dep": "⭐ Stars",
            "btn_crypto_dep": "💎 Крипта",
            "btn_confirm_sync": "✅ Подтвердить ÑÐ¸Ð½Ñ…Ñ€Ð¾Ð½Ð¸Ð·Ð°Ñ†Ð¸ÑŽ",
            "redeem_bonus": "redeem_bonus",
            "claim_bonus_locked": "claim_bonus_locked",
            
            # Mines Game
            "mines_title": "mines_title",
            "mines_select_grid": "mines_select_grid",
            "mines_grid_info": "mines_grid_info",
            "mines_select_mines": "mines_select_mines",
            "mines_enter_bet": "mines_enter_bet",
            "mines_game_info": "mines_game_info",
            "mines_grid": "mines_grid",
            "mines_mines": "mines_mines",
            "mines_diamonds_found": "mines_diamonds_found",
            "mines_safe_remaining": "mines_safe_remaining",
            "mines_bet_amount": "mines_bet_amount",
            "mines_current_multiplier": "mines_current_multiplier",
            "mines_potential_win": "mines_potential_win",
            "mines_profit": "mines_profit",
            "mines_cash_out": "mines_cash_out",
            "mines_game_over": "mines_game_over",
            "mines_game_summary": "mines_game_summary",
            "mines_final_multiplier": "mines_final_multiplier",
            "mines_result": "mines_result",
            "mines_hit_bomb": "mines_hit_bomb",
            "mines_cashed_out": "mines_cashed_out",
            "mines_won": "mines_won",
            "mines_congratulations": "mines_congratulations",
            "mines_final_grid": "mines_final_grid",
            "mines_play_again": "mines_play_again",
            "mines_diamond_found": "mines_diamond_found",
            "mines_tile_opened": "mines_tile_opened",
            "mines_game_expired": "mines_game_expired",
            "mines_game_ended": "mines_game_ended",
            "mines_wait": "mines_wait",
            "mines_min_bet": "mines_min_bet",
            "mines_insufficient_balance": "mines_insufficient_balance",
            "mines_shortage": "mines_shortage",
            "mines_invalid_number": "mines_invalid_number",
            "mines_settings_error": "mines_settings_error",
            
            # Crypto
            "crypto_deposit": "crypto_deposit",
            "crypto_withdraw": "crypto_withdraw",
            "crypto_select_coin": "crypto_select_coin",
            "crypto_deposit_title": "crypto_deposit_title",
            "crypto_deposit_instructions": "crypto_deposit_instructions",
            "crypto_address": "crypto_address",
            "crypto_network": "crypto_network",
            "crypto_network_fee": "crypto_network_fee",
            "crypto_temp_address_note": "crypto_temp_address_note",
            "crypto_expires_in": "crypto_expires_in",
            "crypto_refresh": "crypto_refresh",
            "crypto_back": "crypto_back",
            "crypto_enter_withdraw": "crypto_enter_withdraw",
            "crypto_min_withdraw": "crypto_min_withdraw",
            "crypto_balance": "crypto_balance",
            "crypto_withdraw_sent": "crypto_withdraw_sent",
            "crypto_invalid_address": "crypto_invalid_address",
            "crypto_withdraw_summary": "crypto_withdraw_summary",
        }
    }
    translations["en"]["start_info"] = translations["en"]["welcome"]
    translations["ru"]["start_info"] = translations["ru"]["welcome"]

    # Determine user language
    uid = kwargs.get('user_id')
    if uid and uid in lc.user_languages:
        lang = lc.user_languages[uid]
    else:
        lang = "en"

    # 1) Try inline dict (has en + ru with full HTML templates)
    if lang in translations and key in translations[lang]:
        text = translations[lang][key]
    elif key in translations["en"]:
        # Key exists in inline English but not user lang → try external language file
        ext = get_lang_string(key, lang)
        if ext != key:
            text = ext  # found in external file
        else:
            text = translations["en"][key]  # fallback to inline English
    else:
        # Key not in inline dict at all → try external language files
        text = get_lang_string(key, lang)

    # Fix double-encoded UTF-8 (Cyrillic) when Russian was saved as Latin-1
    if lang == "ru":
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass

    # Format with kwargs if provided
    if kwargs:
        try:
            text = text.format(**kwargs)
        except:
            pass

    return text

def translate_text(text, user_id=None):
    """Auto-translate text based on user's detected language.
    Uses language files for de/fr/zh and the legacy inline map for ru."""
    if not text:
        return text

    # Get user's language
    if user_id and user_id in lc.user_languages:
        user_lang = lc.user_languages[user_id]
    else:
        user_lang = "en"

    # No translation needed for English
    if user_lang == "en":
        return text

    # For de/fr/zh — build translation map from language files (en→target)
    if user_lang in ("de", "fr", "zh"):
        from languages import LANG_STRINGS
        en_strings = LANG_STRINGS.get("en", {})
        target_strings = LANG_STRINGS.get(user_lang, {})
        result = text
        # Sort by length descending so longer phrases match first
        for key in sorted(en_strings.keys(), key=lambda k: len(en_strings[k]), reverse=True):
            en_val = en_strings[key]
            tgt_val = target_strings.get(key)
            if tgt_val and en_val in result:
                result = result.replace(en_val, tgt_val)
        return result

    # For Russian — use the legacy inline map (kept for backward compatibility)
    translations_map = {
        # Errors & Permissions
        "You don't have permission": "You don't have permission",
        "Invalid user ID": "Invalid user ID",
        "User not found": "User not found",
        "Cannot ban an admin": "Cannot ban an admin",
        "is already an admin": "is already an admin",
        "is not an admin": "is not an admin",
        "Cannot remove the main admin": "Cannot remove the main admin",
        "Admin only command": "Admin only command",
        "Only admins can": "Only admins can",
        "Use this command in DM": "Use this command in DM",
        
        # Common actions
        "Operation cancelled": "Operation cancelled",
        "Nothing to cancel": "Nothing to cancel",
        "Please enter a valid number": "Please enter a valid number",
        "Bankroll updated": "Bankroll updated",
        "Minimum withdrawal updated": "Minimum withdrawal updated",
        "Please wait": "Please wait",
        "managers will contact you": "managers will contact you",
        "Please send a screen recording": "Please send a screen recording",
        "Your message has been sent": "Your message has been sent",
        "support team": "support team",
        "We will get back to you shortly": "We will get back to you shortly",
        "ticket is linked to exchange": "ticket is linked to exchange",
        
        # Support
        "How did you top up": "How did you top up",
        "stars to your account": "stars to your account",
        "Which bot do you need help with": "Which bot do you need help with",
        "What seems to be the problem": "What seems to be the problem",
        "My transaction is frozen": "My transaction is frozen",
        "My account is locked": "My account is locked",
        "I didn't receive ton": "I didn't receive ton",
        "Another question": "Another question",
        "Hello": "Hello",
        "Select the exchange": "Select the exchange",
        "No withdrawals found": "No withdrawals found",
        "You don't have any withdrawal history": "You don't have any withdrawal history",
        
        # Tips & Balance
        "Tip amount must be at least": "Tip amount must be at least",
        "Invalid user": "Invalid user",
        "You can't tip yourself": "You can't tip yourself",
        "Insufficient balance": "Insufficient balance",
        "Your balance": "Your balance",
        "Tip amount": "Tip amount",
        
        # Admin
        "Please send a valid name": "Please send a valid name",
        "Please send a valid username": "Please send a valid username",
        "No video is currently set": "No video is currently set",
        "Add new admin": "Add new admin",
        "Remove admin": "Remove admin",
        "View all admins": "View all admins",
        "Test games without betting": "Test games without betting",
        "Set withdraw video": "Set withdraw video",
        "Check video status": "Check video status",
        "Remove video": "Remove video",
        "Send a message to all users": "Send a message to all users",
        
        # Games & Play
        "Choose a game": "Choose a game",
        "Select bet amount": "Select bet amount",
        "Choose rounds": "Choose rounds",
        "Choose throws": "Choose throws",
        "Send your emojis": "Send your emojis",
        "Higher total wins": "Higher total wins",
        "Most rounds won": "Most rounds won",
        "Winner takes the pot": "Winner takes the pot",
        
        # Profile & Stats
        "Your profile": "Your profile",
        "View your profile": "View your profile",
        "View your level": "View your level",
        "View your game history": "View your game history",
        "View top players": "View top players",
        "No players yet": "No players yet",
        "Play a game to appear": "Play a game to appear",
        "on the leaderboard": "on the leaderboard",
        
        # Withdraw
        "Welcome to Stars Withdrawal": "Welcome to Stars Withdrawal",
        "Minimum withdrawal": "Minimum withdrawal",
        "Good to know": "Good to know",
        "When you exchange stars": "When you exchange stars",
        "Telegram keeps a 15% fee": "Telegram keeps a 15% fee",
        "applies a 21-day hold": "applies a 21-day hold",
        "We send TON immediately": "We send TON immediately",
        "factoring in this fee": "factoring in this fee",
        "a small service premium": "a small service premium",
        
        # Deposit
        "Select deposit amount": "Select deposit amount",
        "Custom Amount": "Custom Amount",
        
        # Weekly Bonus
        "Weekly Bonus Available": "Weekly Bonus Available",
        "Total estimated Weekly Bonus": "Total estimated Weekly Bonus",
        "Add": "Add",
        "in your name": "in your name",
        "to get your weekly Boosted": "to get your weekly Boosted",
        
        # Referral
        "Your referral code": "Your referral code",
        "Share this code": "Share this code",
        "Referral earnings": "Referral earnings",
        "Referral balance": "Referral balance",
        
        # Broadcast
        "Broadcast Mode": "Broadcast Mode",
        "Send the message": "Send the message",
        "you want to broadcast": "you want to broadcast",
        "Supports text, photos": "Supports text, photos",
        "videos, audio": "videos, audio",
        "documents": "documents",
        "Use /cancel to exit": "Use /cancel to exit",
        "Broadcast finished": "Broadcast finished",
        "Total users": "Total users",
        "Sent": "Sent",
        "Failed": "Failed",
        
        # Cancel
        "Operation cancelled": "Operation cancelled",
        "Nothing to cancel": "Nothing to cancel",
        
        # Error handler
        "An unexpected error occurred": "An unexpected error occurred",
        "Please try again later": "Please try again later",
        "If the problem persists": "If the problem persists",
        "contact support": "contact support",
    }
    
    # Apply translations (case-insensitive where possible)
    result = text
    for eng, rus in translations_map.items():
        # Replace with case preservation
        import re
        pattern = re.compile(re.escape(eng), re.IGNORECASE)
        result = pattern.sub(rus, result)
    
    # Fix double-encoded UTF-8 (Cyrillic) when Russian was saved as Latin-1
    try:
        result = result.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return result
