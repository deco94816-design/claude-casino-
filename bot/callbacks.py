# -*- coding: utf-8 -*-
"""Inline callback-query dispatcher (button_callback).

Large CallbackQueryHandler routing every non-delegated callback_data prefix
(menus, leaderboard, deposit, bets, cashout, etc.). Lifted VERBATIM -- behaviour
byte-for-byte identical -- except the global-state bridge: rebound money/state
globals via ``lc.*`` so in-place balance/history mutations hit the live dicts.
Local ``import games.* as ..`` stay inside the function. _build_lb_caption,
_build_lb_keyboard and cf_challenge_timeout are undefined in the original module
too; left bare so those branches raise the same NameError (caught upstream).
Re-imported last so all earlier delegated handlers are already on lc.
"""

import asyncio
import os
import random
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import games.claw as claw

import librate_casino as lc
from librate_casino import (
    BOT_USERNAME, CASINO_LEVELS, CF_MULTIPLIER, GAME_CONFIG, GAME_TYPES, LEADERBOARD_DATA,
    LEADERBOARD_IMAGES, MATCHES_PER_PAGE, MATCH_ID_BASE, MULTIPLIERS, RANKS, SUPPORTED_LANGS,
    adjust_user_balance, build_copy_turn_reply_markup, check_bot_name_in_profile, coinflip_stickers, db, detect_lang,
    format_matches_page, game_sessions, get_cf_menu, get_or_create_profile, get_rank_info, get_user_balance,
    get_user_level, get_user_link, get_user_rank, get_weekly_bonus_amount, handle_blackjack_callback, handle_errors,
    handle_predict_callback, handle_steal_callback, handle_support_callback, is_admin, is_banned, is_frozen,
    logger, menu_owners, register_menu_owner, send_invoice, send_or_edit_history, start_bot_game,
    sync_settings_to_bot, t, translate_text, update_game_stats, user_languages, user_weekly_bonus_data,
)


@handle_errors
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    if data.startswith("pvp_"):
        import games.pvp as pvp
        await pvp.handle_pvp_callback(update, context)
        return
        
    if data.startswith("tower_"):
        import games.tower as tower
        await tower.handle_tower_callback(update, context)
        return

    if data.startswith("deposit_"):
        if data == "deposit_custom":
            await query.answer()
            await query.message.reply_html(
                "💬 To deposit a custom amount, use the command:\n<code>/deposit [amount]</code>"
            )
            return
            
        try:
            amount = int(data.split("_")[1])
            await send_invoice(query, amount)
        except ValueError:
            await query.answer("Invalid deposit amount.", show_alert=True)
        return

    # Coinflip Phase 1 callbacks
    if data == "cf_toggle_curr":
        use_stars = context.user_data.get('cf_use_stars', False)
        context.user_data['cf_use_stars'] = not use_stars
        balance = get_user_balance(user_id)
        text, markup = get_cf_menu(user_id, balance, context.user_data['cf_use_stars'])
        await query.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
        return
        
    if data.startswith("cf_bet_btn_"):
        try:
            bet_amount = int(data.split("_")[-1])
        except ValueError:
            bet_amount = 1
        
        balance = get_user_balance(user_id)
        if balance < bet_amount and not is_admin(user_id):
            await query.answer("❌ Insufficient balance!", show_alert=True)
            return
            
        await query.message.delete()
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
            chat_id=query.message.chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        
        context.job_queue.run_once(
            cf_challenge_timeout, 
            60, 
            data={
                'chat_id': query.message.chat_id, 
                'message_id': sent_msg.message_id,
                'user_id': user_id,
                'bet_stars': bet_amount
            },
            name=f"cf_timeout_{sent_msg.message_id}"
        )
        return

    # Auto-detect language on callback if not already set
    if user_id not in user_languages:
        user_lang_code = getattr(query.from_user, 'language_code', None) or ""
        detected = detect_lang(user_lang_code)
        user_languages[user_id] = detected
        db.set_user_language(user_id, detected)

    # Check if user is banned (allow admins)
    if is_banned(user_id) and not is_admin(user_id):
        await query.answer()
        return  # Silently ignore banned users

    # Check if user is frozen (block deposit, withdraw, game callbacks)
    if is_frozen(user_id) and not is_admin(user_id):
        frozen_prefixes = (
            'deposit_', 'withdraw_', 'crypto_deposit', 'play_game_',
            'game_', 'bet_', 'mines_', 'pred_', 'cflip_', 'bj_',
        )
        if any(data.startswith(p) for p in frozen_prefixes):
            await query.answer(t("err_frozen", user_id=user_id), show_alert=True)
            return

    # Callback ownership protection
    key = (query.message.chat_id, query.message.message_id)
    owner_id = menu_owners.get(key)
    if owner_id and owner_id != user_id:
        await query.answer(t("err_not_your_menu", user_id=user_id), show_alert=True)
        return
    
    try:
        # Handle claw machine callbacks
        if data.startswith("claw_"):
            import games.claw as claw
            await claw.handle_claw_callback(update, context)
            return

        # Handle language selection callbacks
        if data.startswith("set_lang_"):
            new_lang = data.replace("set_lang_", "")
            if new_lang in SUPPORTED_LANGS:
                user_languages[user_id] = new_lang
                db.set_user_language(user_id, new_lang)
                lang_names = {"en": "English", "ru": "Ð ÑÑÑÐºÐ¸Ð¹", "de": "Deutsch", "fr": "Français", "zh": "中文"}
                lang_name = lang_names.get(new_lang, new_lang)
                await query.answer(f"✅ {lang_name}", show_alert=False)
                await query.edit_message_text(
                    f"✅ <b>Language changed to {lang_name}!</b>",
                    parse_mode=ParseMode.HTML
                )
            else:
                await query.answer(t("err_unsupported_lang", user_id=user_id), show_alert=True)
            return

        # Handle predict game callbacks
        if data.startswith("pred_"):
            await handle_predict_callback(update, context)
            return

        # Handle steal command callbacks
        if data.startswith("steal_"):
            await query.answer()
            await handle_steal_callback(update, context)
            return

        # Handle bot network callbacks
        if data.startswith("network_"):
            await query.answer()
            if data == "network_sync_confirm":
                bot_info = context.user_data.pop("sync_target_bot", None)
                if not bot_info:
                    await query.edit_message_text(t("sync_expired", user_id=user_id))
                    return
                source_path = os.path.abspath(db.path)
                target_path = bot_info["db_path"]
                try:
                    synced = sync_settings_to_bot(source_path, target_path)
                    details = "\n".join(f"  • {k}: {v}" for k, v in synced.items())
                    await query.edit_message_text(
                        f"✅ <b>Sync completed to {bot_info['name']}!</b>\n\n"
                        f"<b>Synced:</b>\n{details}",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    await query.edit_message_text(f"❌ Sync failed: {e}")
            elif data == "network_sync_cancel":
                context.user_data.pop("sync_target_bot", None)
                await query.edit_message_text(t("sync_cancelled", user_id=user_id))
            return

        # Handle leaderboard category switches
        if data.startswith("lb_"):
            cat_key = data.replace("lb_", "")
            if cat_key in LEADERBOARD_DATA:
                await query.answer()
                caption = _build_lb_caption(cat_key)
                markup = _build_lb_keyboard()
                try:
                    with open(LEADERBOARD_IMAGES[cat_key], "rb") as img:
                        media = InputMediaPhoto(media=img, caption=caption, parse_mode=ParseMode.HTML)
                        await query.edit_message_media(media=media, reply_markup=markup)
                except Exception:
                    pass
                return

        # Handle support ticket callbacks
        if data.startswith("support_"):
            logger.info(f"Routing support callback: {data} to handle_support_callback")
            await handle_support_callback(update, context)
            return

        # Handle blackjack callbacks (before generic query.answer)
        if data.startswith("bj_"):
            await handle_blackjack_callback(update, context)
            return

        # Handle bonus menu navigation
        if data == "close_history":
            try:
                await query.message.delete()
            except:
                pass
            return
            
        if data.startswith("history_page_"):
            page = int(data.split("_")[-1])
            await send_or_edit_history(query, user_id, page)
            return

        if data == "bonus_main":
            text = "⭐ Receive bonuses for activity and games"
            keyboard = [
                [InlineKeyboardButton("🏆 Rank bonus", callback_data="bonus_rank")],
                [InlineKeyboardButton("🎁 Weekly bonus", callback_data="bonus_weekly")],
                [InlineKeyboardButton("🔄 Rakeback", callback_data="bonus_rakeback")],
                [InlineKeyboardButton("💎 Reload", callback_data="bonus_reload")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "bonus_rank":
            profile = get_or_create_profile(user_id)
            current_rank_level = get_user_rank(profile.get("total_bets", 0.0) * lc.STARS_TO_USD)
            rank_info = get_rank_info(current_rank_level)
            claimed_ranks = profile.get("claimed_ranks", [])
            
            unclaimed_bonus = 0.0
            rank_to_claim = 0
            for r in range(1, current_rank_level + 1):
                if r not in claimed_ranks:
                    rank_to_claim = r
                    unclaimed_bonus = RANKS[r]["bonus"]
                    break
            
            if rank_to_claim > 0:
                btn = InlineKeyboardButton("🏆 Claim rank bonus", callback_data=f"claim_rank_{rank_to_claim}")
            else:
                btn = InlineKeyboardButton("🔒 Claim rank bonus", callback_data="claim_rank_locked")
                
            text = (
                f"🏆 Rank bonus\n\n"
                f"ℹ️ Receive a bonus for reaching a new rank!\n"
                f"The higher your rank — the bigger the bonus.\n\n"
                f"💵 Your rank bonus: ${unclaimed_bonus:.2f}\n"
                f"🥇 Current rank: {rank_info['name']}"
            )
            keyboard = [
                [btn],
                [InlineKeyboardButton("📋 Rank List", callback_data="bonus_rank_list_1")],
                [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data.startswith("bonus_rank_list_"):
            page = int(data.split("_")[-1])
            profile = get_or_create_profile(user_id)
            total_bets_usd = profile.get("total_bets", 0.0) * lc.STARS_TO_USD
            current_rank_level = get_user_rank(total_bets_usd)
            
            # Max pages = 11 (3 ranks per page)
            total_pages = 11
            start_idx = (page - 1) * 3 + 1
            end_idx = min(start_idx + 2, len(RANKS))
            
            text_blocks = []
            for r in range(start_idx, end_idx + 1):
                if r not in RANKS:
                    continue
                rank = RANKS[r]
                emoji = rank["emoji"]
                tier = rank["tier"]
                
                block = f"<blockquote expandable>🔴 {emoji} <b>{rank['name']}</b>\n"
                block += f"<i><b>💵 Bonus: ${rank['bonus']:.2f}</b></i>\n"
                block += f"<i><b>💎 Required wager: ${rank['wager_required']:,.2f}</b></i>\n"
                
                # If this is the user's current rank, show progress
                if r == current_rank_level:
                    next_wager = RANKS.get(r + 1, rank)["wager_required"]
                    current_wager = rank["wager_required"]
                    if next_wager > current_wager:
                        progress_pct = ((total_bets_usd - current_wager) / (next_wager - current_wager)) * 100
                        progress_pct = max(0, min(100, progress_pct))
                    else:
                        progress_pct = 100.0
                    
                    block += f"\n🎯 Progress: {progress_pct:.2f}%\n"
                    
                    filled_chars = int(progress_pct / 10)
                    empty_chars = 10 - filled_chars
                    bar = "█" * filled_chars + "░" * empty_chars
                    block += f"[{bar}] {emoji}\n"
                    
                    if next_wager > current_wager:
                        remaining = next_wager - total_bets_usd
                        if remaining < 0: remaining = 0
                        block += f"<b>Remaining until rank up: ${remaining:,.2f}</b>\n"
                
                if rank["perks"]:
                    # Ensure formatting is maintained for perks
                    perks = rank["perks"].split("\n")
                    formatted_perks = "\n".join([f"<i>{p}</i>" if p.startswith("✨") else f"<i>✨ {p}</i>" for p in perks])
                    block += f"\n{formatted_perks}\n"
                
                block += "</blockquote>"
                text_blocks.append(block)
                
            text = "\n\n".join(text_blocks)
            
            # Pagination buttons
            nav_buttons = []
            if page > 1:
                nav_buttons.append(InlineKeyboardButton("←", callback_data=f"bonus_rank_list_{page-1}"))
            else:
                nav_buttons.append(InlineKeyboardButton("←", callback_data="ignore"))
                
            if page < total_pages:
                nav_buttons.append(InlineKeyboardButton("→", callback_data=f"bonus_rank_list_{page+1}"))
            else:
                nav_buttons.append(InlineKeyboardButton("→", callback_data="ignore"))
                
            keyboard = [
                nav_buttons,
                [InlineKeyboardButton("⬅️ Back", callback_data="bonus_rank")]
            ]
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "claim_rank_locked":
            await query.answer("You've already claimed bonus for this rank", show_alert=True)
            return

        if data.startswith("claim_rank_"):
            rank_id = int(data.split("_")[-1])
            profile = get_or_create_profile(user_id)
            claimed_ranks = profile.get("claimed_ranks", [])
            
            if rank_id in claimed_ranks:
                await query.answer("You've already claimed bonus for this rank", show_alert=True)
                return
                
            bonus_usd = RANKS[rank_id]["bonus"]
            bonus_stars = max(1, int(bonus_usd / lc.STARS_TO_USD))
            
            adjust_user_balance(user_id, bonus_stars)
            claimed_ranks.append(rank_id)
            
            db.update_profile(
                user_id,
                total_games=profile["total_games"],
                total_bets=profile["total_bets"],
                total_wins=profile["total_wins"],
                total_losses=profile["total_losses"],
                games_won=profile["games_won"],
                games_lost=profile["games_lost"],
                favorite_game=profile["favorite_game"],
                biggest_win=profile["biggest_win"],
                game_counts=profile["game_counts"],
                rakeback_balance=profile.get("rakeback_balance", 0.0),
                claimed_ranks=claimed_ranks,
                last_reload_claim=profile.get("last_reload_claim")
            )
            
            await query.answer(f"✅ Rank bonus of ${bonus_usd:.2f} credited to your balance!", show_alert=True)
            current_rank_level = get_user_rank(profile.get("total_bets", 0.0) * lc.STARS_TO_USD)
            rank_info = get_rank_info(current_rank_level)
            unclaimed_bonus = 0.0
            rank_to_claim = 0
            for r in range(1, current_rank_level + 1):
                if r not in claimed_ranks:
                    rank_to_claim = r
                    unclaimed_bonus = RANKS[r]["bonus"]
                    break
            if rank_to_claim > 0:
                btn = InlineKeyboardButton("🏆 Claim rank bonus", callback_data=f"claim_rank_{rank_to_claim}")
            else:
                btn = InlineKeyboardButton("🔒 Claim rank bonus", callback_data="claim_rank_locked")
            text = (
                f"🏆 Rank bonus\n\n"
                f"ℹ️ Receive a bonus for reaching a new rank!\n"
                f"The higher your rank — the bigger the bonus.\n\n"
                f"💵 Your rank bonus: ${unclaimed_bonus:.2f}\n"
                f"🥇 Current rank: {rank_info['name']}"
            )
            keyboard = [[btn], [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "bonus_weekly":
            from datetime import timezone
            now = datetime.now(timezone.utc)
            days_ahead = 5 - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_saturday = now + timedelta(days=days_ahead)
            next_saturday = next_saturday.replace(hour=0, minute=0, second=0, microsecond=0)
            
            diff = next_saturday - now
            days, seconds = diff.days, diff.seconds
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60
            countdown = f"{days}d {hours}h {minutes}m {seconds}s"
            
            is_saturday = now.weekday() == 5
            
            bonus_data = user_weekly_bonus_data.get(user_id)
            iso_year, iso_week, _ = now.isocalendar()
            current_iso_week = (iso_year, iso_week)
            
            if bonus_data and tuple(bonus_data.get("iso_week", ())) == current_iso_week:
                bonus_stars = bonus_data.get("amount_stars", 20)
                claimed = bonus_data.get("claimed", False)
            else:
                import random
                bonus_stars = random.randint(20, 100)
                claimed = False
                user_weekly_bonus_data[user_id] = {
                    "iso_week": current_iso_week,
                    "amount_stars": bonus_stars,
                    "claimed": False
                }
                
            display_name = query.from_user.first_name or ""
            if query.from_user.last_name:
                display_name += f" {query.from_user.last_name}"
            
            has_name_bonus = "@Librateds" in display_name or "Librateds" in display_name
            final_stars = int(bonus_stars * 1.1) if has_name_bonus else bonus_stars
            bonus_usd = final_stars * lc.STARS_TO_USD
            
            if is_saturday and not claimed:
                btn = InlineKeyboardButton("🎁 Claim bonus", callback_data="claim_weekly_bonus")
            else:
                btn = InlineKeyboardButton("🔒 Claim bonus", callback_data="claim_weekly_locked")
                
            text = (
                f"🎁 Receive a bonus every Saturday\n\n"
                f"If you don't claim it during Saturday — it expires\n"
                f"⚠️ Next bonus available in {countdown}\n\n"
                f"> Add @Librateds to your name and get an extra +10% bonus\n\n"
                f"💵 Your bonus: ${bonus_usd:.2f}"
            )
            keyboard = [[btn], [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "claim_weekly_locked":
            await query.answer("Bonus only available on Saturdays or already claimed", show_alert=True)
            return
            
        if data == "claim_weekly_bonus":
            from datetime import timezone
            now = datetime.now(timezone.utc)
            is_saturday = now.weekday() == 5
            
            if not is_saturday:
                await query.answer("Bonus is only available on Saturdays!", show_alert=True)
                return
                
            bonus_data = user_weekly_bonus_data.get(user_id)
            if not bonus_data:
                await query.answer("No bonus data found.", show_alert=True)
                return
                
            if bonus_data.get("claimed", False):
                await query.answer("You've already claimed your weekly bonus!", show_alert=True)
                return
                
            bonus_stars = bonus_data.get("amount_stars", 20)
            display_name = query.from_user.first_name or ""
            if query.from_user.last_name:
                display_name += f" {query.from_user.last_name}"
            has_name_bonus = "@Librateds" in display_name or "Librateds" in display_name
            final_stars = int(bonus_stars * 1.1) if has_name_bonus else bonus_stars
            bonus_usd = final_stars * lc.STARS_TO_USD
            
            adjust_user_balance(user_id, final_stars)
            user_weekly_bonus_data[user_id]["claimed"] = True
            
            await query.answer(f"✅ Weekly bonus of ${bonus_usd:.2f} credited to your balance!", show_alert=True)
            
            days_ahead = 5 - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_saturday = now + timedelta(days=days_ahead)
            next_saturday = next_saturday.replace(hour=0, minute=0, second=0, microsecond=0)
            diff = next_saturday - now
            days, seconds = diff.days, diff.seconds
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60
            countdown = f"{days}d {hours}h {minutes}m {seconds}s"
            
            text = (
                f"🎁 Receive a bonus every Saturday\n\n"
                f"If you don't claim it during Saturday — it expires\n"
                f"⚠️ Next bonus available in {countdown}\n\n"
                f"> Add @Librateds to your name and get an extra +10% bonus\n\n"
                f"💵 Your bonus: ${bonus_usd:.2f}"
            )
            keyboard = [[InlineKeyboardButton("🔒 Claim bonus", callback_data="claim_weekly_locked")], [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "bonus_rakeback":
            profile = get_or_create_profile(user_id)
            rakeback_stars = profile.get("rakeback_balance", 0.0)
            current_rank_level = get_user_rank(profile.get("total_bets", 0.0) * lc.STARS_TO_USD)
            
            if current_rank_level < 2:  # Bronze I
                btn = InlineKeyboardButton("🔒 Claim rakeback", callback_data="claim_rakeback_norank")
            elif rakeback_stars <= 0:
                btn = InlineKeyboardButton("🔒 Claim rakeback", callback_data="claim_rakeback_empty")
            else:
                btn = InlineKeyboardButton("💸 Claim rakeback", callback_data="claim_rakeback")
                
            text = (
                f"ℹ️ Rakeback is a return of part of your loss as a bonus.\n"
                f"🏆 Available only from Bronze I rank and above!\n\n"
                f"💵 Rakeback balance: ${(rakeback_stars * lc.STARS_TO_USD):.2f}"
            )
            keyboard = [[btn], [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
            
        if data == "claim_rakeback_norank":
            await query.answer("You need Bronze I rank to claim rakeback", show_alert=True)
            return
            
        if data == "claim_rakeback_empty":
            await query.answer("No rakeback available yet", show_alert=True)
            return
            
        if data == "claim_rakeback":
            profile = get_or_create_profile(user_id)
            rakeback_stars = profile.get("rakeback_balance", 0.0)
            
            if rakeback_stars > 0:
                adjust_user_balance(user_id, rakeback_stars)
                rakeback_usd = rakeback_stars * lc.STARS_TO_USD
                
                db.update_profile(
                    user_id,
                    total_games=profile["total_games"],
                    total_bets=profile["total_bets"],
                    total_wins=profile["total_wins"],
                    total_losses=profile["total_losses"],
                    games_won=profile["games_won"],
                    games_lost=profile["games_lost"],
                    favorite_game=profile["favorite_game"],
                    biggest_win=profile["biggest_win"],
                    game_counts=profile["game_counts"],
                    rakeback_balance=0.0,
                    claimed_ranks=profile.get("claimed_ranks", []),
                    last_reload_claim=profile.get("last_reload_claim")
                )
                await query.answer(f"✅ Rakeback of ${rakeback_usd:.2f} credited to your balance!", show_alert=True)
                
                text = (
                    f"ℹ️ Rakeback is a return of part of your loss as a bonus.\n"
                    f"🏆 Available only from Bronze I rank and above!\n\n"
                    f"💵 Rakeback balance: $0.00"
                )
                keyboard = [[InlineKeyboardButton("🔒 Claim rakeback", callback_data="claim_rakeback_empty")], [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]]
                await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return

        if data == "bonus_reload":
            profile = get_or_create_profile(user_id)
            current_rank_level = get_user_rank(profile.get("total_bets", 0.0) * lc.STARS_TO_USD)
            
            from datetime import timezone
            now = datetime.now(timezone.utc)
            iso_year, iso_week, _ = now.isocalendar()
            current_iso_week_str = f"{iso_year}-{iso_week}"
            
            last_reload = profile.get("last_reload_claim")
            
            if current_rank_level < 14:
                btn = InlineKeyboardButton("🔒 Claim reload", callback_data="claim_reload_norank")
            elif last_reload == current_iso_week_str:
                btn = InlineKeyboardButton("🔒 Claim reload", callback_data="claim_reload_claimed")
            else:
                btn = InlineKeyboardButton("⭐ Claim reload", callback_data="claim_reload")
                
            text = (
                f"👑 Receive a weekly Reload for your activity\n\n"
                f"⚠️ Reload available from rank\n"
                f"◇ Diamond I"
            )
            keyboard = [[btn], [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
            
        if data == "claim_reload_norank":
            await query.answer("Reload available from Diamond I rank and above", show_alert=True)
            return
            
        if data == "claim_reload_claimed":
            from datetime import timezone
            now = datetime.now(timezone.utc)
            days_ahead = 7 - now.weekday()
            next_monday = now + timedelta(days=days_ahead)
            next_monday = next_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            diff = next_monday - now
            days, seconds = diff.days, diff.seconds
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            await query.answer(f"Already claimed this week. Next reload in {days}d {hours}h {minutes}m", show_alert=True)
            return
            
        if data == "claim_reload":
            profile = get_or_create_profile(user_id)
            from datetime import timezone
            now = datetime.now(timezone.utc)
            iso_year, iso_week, _ = now.isocalendar()
            current_iso_week_str = f"{iso_year}-{iso_week}"
            
            reload_usd = 10.00
            reload_stars = max(1, int(reload_usd / lc.STARS_TO_USD))
            
            adjust_user_balance(user_id, reload_stars)
            
            db.update_profile(
                user_id,
                total_games=profile["total_games"],
                total_bets=profile["total_bets"],
                total_wins=profile["total_wins"],
                total_losses=profile["total_losses"],
                games_won=profile["games_won"],
                games_lost=profile["games_lost"],
                favorite_game=profile["favorite_game"],
                biggest_win=profile["biggest_win"],
                game_counts=profile["game_counts"],
                rakeback_balance=profile.get("rakeback_balance", 0.0),
                claimed_ranks=profile.get("claimed_ranks", []),
                last_reload_claim=current_iso_week_str
            )
            
            await query.answer(f"✅ Reload bonus of ${reload_usd:.2f} credited to your balance!", show_alert=True)
            
            text = (
                f"👑 Receive a weekly Reload for your activity\n\n"
                f"⚠️ Reload available from rank\n"
                f"◇ Diamond I"
            )
            keyboard = [[InlineKeyboardButton("🔒 Claim reload", callback_data="claim_reload_claimed")], [InlineKeyboardButton("⬅️ Back", callback_data="bonus_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            return
        
        # Handle matches pagination
        if data.startswith("matches_page_"):
            page = int(data.replace("matches_page_", ""))
            history = lc.user_game_history.get(user_id, [])

            if not history:
                await query.answer(t("err_no_match_history", user_id=user_id), show_alert=True)
                return

            total = len(history)
            history_reversed = []
            for i, entry in enumerate(reversed(history)):
                entry_copy = dict(entry)
                entry_copy['match_id'] = MATCH_ID_BASE + total - i
                history_reversed.append(entry_copy)

            total_pages = max(1, (len(history_reversed) + MATCHES_PER_PAGE - 1) // MATCHES_PER_PAGE)
            page = max(0, min(page, total_pages - 1))

            text = format_matches_page(history_reversed, page, total_pages)

            buttons = []
            if page > 0:
                buttons.append(InlineKeyboardButton("¢¬â¦¯¸", callback_data=f"matches_page_{page - 1}"))
            if page < total_pages - 1:
                buttons.append(InlineKeyboardButton("âž¡ï¸¯¸", callback_data=f"matches_page_{page + 1}"))
            keyboard = [buttons] if buttons else []
            keyboard.append([InlineKeyboardButton(t("back_button", user_id=user_id), callback_data="matches_back")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            await query.answer()
            return

        if data == "matches_back":
            await query.edit_message_text(t("history_closed", user_id=user_id), parse_mode=ParseMode.HTML)
            await query.answer()
            return
        
        # Answer callback for other handlers
        await query.answer()
        
        # Old game_repeat/game_double removed - new system uses inline flow
        
        # Handle weekly bonus redemption
        if data == "redeem_weekly_bonus":
            user = query.from_user
            
            # Check if it's Saturday
            if not is_saturday():
                await query.edit_message_text(
                    "❌ <b>No bonus available</b>",
                    parse_mode=ParseMode.HTML
                )
                return
            
            # Check if user has already claimed this Saturday
            last_claim = lc.user_weekly_bonus_claimed.get(user_id)
            if last_claim:
                now = datetime.now()
                # Check if last claim was on a Saturday and it's the same date (same Saturday)
                if last_claim.weekday() == 5 and last_claim.date() == now.date():
                    await query.answer(t("err_bonus_claimed_today", user_id=user_id), show_alert=True)
                    return
                # If last claim was on a Saturday but different date, allow (it's a new Saturday)
            
            # Check if user has bot name in profile
            bot_name = lc.bot_identity.get("name", BOT_USERNAME)
            if not check_bot_name_in_profile(user):
                await query.answer(
                    f"❌ Add @{bot_name} to your profile name to claim the weekly bonus!",
                    show_alert=True
                )
                return
            
            # Give random weekly bonus
            weekly_bonus = get_weekly_bonus_amount()
            adjust_user_balance(user_id, weekly_bonus)
            claim_date = datetime.now()
            lc.user_weekly_bonus_claimed[user_id] = claim_date  # Keep in memory for compatibility
            db.set_weekly_bonus_claimed(user_id, claim_date)
            
            balance = get_user_balance(user_id)
            balance_usd = balance * lc.STARS_TO_USD
            
            await query.edit_message_text(
                f"🎂 <b>Weekly Bonus Claimed Successfully!</b>\n\n"
                f"✅ We found <b>@{bot_name}</b> in your profile name!\n\n"
                f"💰 You received: <b>{weekly_bonus} ⭐</b>\n"
                f"💵 New Balance: <b>{balance:,} ⭐</b> (${balance_usd:.2f})\n\n"
                f"🎉 Thank you for supporting us!\n\n"
                f"¢° Next weekly bonus available next Saturday!",
                parse_mode=ParseMode.HTML
            )
            
            logger.info(f"Weekly bonus claimed by user {user_id} ({user.first_name})")
            return
        
        # Handle balance inline buttons
        if data == "balance_deposit":
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
                    InlineKeyboardButton(t("back_button", user_id=user_id), callback_data="back_to_balance"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            sent_dep = await query.edit_message_text(
                "💳 <b>Select deposit amount:</b>",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            register_menu_owner(sent_dep, user_id)
            return
        
        if data == "balance_withdraw":
            if query.message.chat.type != "private":
                bot_info = await context.bot.get_me()
                await query.edit_message_text(
                    "🔒 <b>Private Command Only</b>\n\n"
                    "For your security, withdrawals can only be done in a private chat with the bot.\n\n"
                    f"👉 <a href='https://t.me/{bot_info.username}?start=withdraw'>Click here to open DM</a>\n\n"
                    "Then use /withdraw command.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            context.user_data['withdraw_state'] = None
            context.user_data['withdraw_amount'] = None
            context.user_data['withdraw_address'] = None
            
            welcome_text = (
                "✅ <b>Welcome to Stars Withdrawal!</b>\n\n"
                "<b>Withdraw:</b>\n"
                "1 ⭐ = $0.0179 = 0.01201014 TON\n\n"
                f"<b>Minimum withdrawal: {lc.MIN_WITHDRAWAL} ⭐</b>\n\n"
                "<blockquote>â¹ï¸  <b>Good to know:</b>\n"
                "• When you exchange stars through a channel or bot, Telegram keeps a 15% fee and applies a 21-day hold.\n"
                "• We send TON immediately—factoring in this fee and a small service premium.</blockquote>"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton(t("withdraw_stars_button", user_id=user_id), callback_data="withdraw_stars"),
                    InlineKeyboardButton(t("withdraw_crypto_button", user_id=user_id), callback_data="withdraw_crypto"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # For callback, we need to handle video differently
            # If video is set, delete current message and send new one with video
            if lc.withdraw_video_file_id:
                try:
                    await query.message.delete()
                    sent_msg = await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=lc.withdraw_video_file_id,
                        caption=welcome_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )
                    register_menu_owner(sent_msg, user_id)
                except Exception as e:
                    logger.error(f"Failed to send withdraw video in callback: {e}")
                    sent_edit = await query.edit_message_text(
                        welcome_text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.HTML
                    )
                    register_menu_owner(sent_edit, user_id)
            else:
                sent_edit = await query.edit_message_text(
                    welcome_text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
                register_menu_owner(sent_edit, user_id)
            return
        
        # Handle addbal callbacks
        if data.startswith("addbal_stars_"):
            try:
                # Format: addbal_stars_USERID_AMOUNT (amount may have DOT instead of .)
                parts = data.split("_", 3)  # Split into max 4 parts
                if len(parts) >= 4:
                    target_user_id = int(parts[2])
                    amount_str = parts[3].replace('DOT', '.')  # Replace DOT back to .
                    amount = float(amount_str)
                    
                    # Add stars balance (use db directly to bypass admin guard)
                    db.adjust_user_balance(target_user_id, amount)
                    new_balance = db.get_user_balance(target_user_id)
                    lc.user_balances[target_user_id] = new_balance  # Sync memory cache
                    
                    await query.edit_message_text(
                        f"✅ <b>Balance Added Successfully!</b>\n\n"
                        f"👤 User ID: <code>{target_user_id}</code>\n"
                        f"⭐ Added: <b>{amount:,.2f} Stars</b>\n"
                        f"💰 New Balance: <b>{new_balance:,.2f} Stars</b>",
                        parse_mode=ParseMode.HTML
                    )
                    logger.info(f"Admin {user_id} added {amount} stars to user {target_user_id}")
                else:
                    await query.answer(t("err_invalid_data", user_id=user_id), show_alert=True)
            except (ValueError, IndexError) as e:
                await query.answer(t("err_processing", user_id=user_id), show_alert=True)
                logger.error(f"Error in addbal_stars callback: {e}")
            return
        
        if data.startswith("addbal_crypto_"):
            try:
                # Format: addbal_crypto_USERID_AMOUNT (amount may have DOT instead of .)
                parts = data.split("_", 3)  # Split into max 4 parts
                if len(parts) >= 4:
                    target_user_id = int(parts[2])
                    amount_str = parts[3].replace('DOT', '.')  # Replace DOT back to .
                    amount = float(amount_str)
                    
                    # Add crypto balance
                    db.adjust_user_crypto_balance(target_user_id, amount)
                    lc.user_crypto_balances[target_user_id] = db.get_user_crypto_balance(target_user_id)
                    
                    new_crypto_balance = lc.user_crypto_balances[target_user_id]
                    
                    await query.edit_message_text(
                        f"✅ <b>Crypto Balance Added Successfully!</b>\n\n"
                        f"👤 User ID: <code>{target_user_id}</code>\n"
                        f"💎 Added: <b>${amount:,.2f}</b>\n"
                        f"💰 New Crypto Balance: <b>${new_crypto_balance:,.2f}</b>",
                        parse_mode=ParseMode.HTML
                    )
                    logger.info(f"Admin {user_id} added ${amount} crypto to user {target_user_id}")
                else:
                    await query.answer(t("err_invalid_data", user_id=user_id), show_alert=True)
            except (ValueError, IndexError) as e:
                await query.answer(t("err_processing", user_id=user_id), show_alert=True)
                logger.error(f"Error in addbal_crypto callback: {e}")
            return
        
        # Mines callbacks -> games/mines/handlers.py
        if data.startswith("mines_") or data.startswith("mine_click_"):
            import games.mines.handlers as mines
            await mines.handle_mines_callback(update, context)
            return
        
        if data == "back_to_menu":
            menu_kb = [
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
            sent_menu = await query.edit_message_text(
                "🎮 <b>Menu</b>\nChoose the action:",
                reply_markup=InlineKeyboardMarkup(menu_kb),
                parse_mode=ParseMode.HTML
            )
            register_menu_owner(sent_menu, user_id)
            return

        if data == "back_to_balance":
            balance = get_user_balance(user_id)
            balance_usd = balance * lc.STARS_TO_USD
            admin_note = " (Admin - Unlimited)" if is_admin(user_id) else ""

            keyboard = [
                [
                    InlineKeyboardButton(t("btn_deposit_inline", user_id=user_id), callback_data="balance_deposit"),
                    InlineKeyboardButton(t("btn_withdraw_inline", user_id=user_id), callback_data="balance_withdraw"),
                ],
                [
                    InlineKeyboardButton(t("back_button", user_id=user_id), callback_data="back_to_menu"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            sent_balance = await query.edit_message_text(
                f"💰 <b>Your Balance</b>{admin_note}\n\n"
                f"⭐ Stars: <b>{balance:,} ⭐</b>\n"
                f"💵 USD: <b>${balance_usd:.2f}</b>",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            register_menu_owner(sent_balance, user_id)
            return

        if data == "show_profile":
            user = query.from_user
            profile = get_or_create_profile(user_id, user.username or user.first_name)
            balance = get_user_balance(user_id)
            balance_usd = balance * lc.STARS_TO_USD
            total_bets = float(profile.get('total_bets', 0) or 0)
            total_wins = float(profile.get('total_wins', 0) or 0)
            total_bets_usd = total_bets * lc.STARS_TO_USD
            total_wins_usd = total_wins * lc.STARS_TO_USD
            total_games = profile.get('total_games', 0)
            try:
                current_level = get_user_level(total_bets_usd)
                current_level = max(0, min(25, current_level))
                level_info = CASINO_LEVELS.get(current_level, CASINO_LEVELS[0])
                rank_name = level_info.get('name', 'Steel')
            except Exception:
                rank_name = "Steel"
            fav_game = profile.get('favorite_game')
            if fav_game and fav_game in GAME_TYPES:
                fav_game_display = f"{GAME_TYPES[fav_game]['icon']} {GAME_TYPES[fav_game]['name']}"
            elif fav_game and fav_game in GAME_CONFIG:
                fav_game_display = f"{GAME_CONFIG[fav_game]['emoji']} {GAME_CONFIG[fav_game]['name']}"
            else:
                fav_game_display = "None"
            biggest_win = profile.get('biggest_win', 0)
            biggest_win_usd = biggest_win * lc.STARS_TO_USD if biggest_win > 0 else 0.0

            stats_kb = [[InlineKeyboardButton(t("back_button", user_id=user_id), callback_data="back_to_menu")]]
            stats_text = (
                f"📊 <b>Your Stats</b>\n\n"
                f"🏅 Rank: {rank_name}\n"
                f"💰 Balance: <b>${balance_usd:.2f}</b>\n\n"
                f"⚡ Total games: <b>{total_games}</b>\n"
                f"💵 Total wagered: <b>${total_bets_usd:.2f}</b>\n"
                f"💸 Total winnings: <b>${total_wins_usd:.2f}</b>\n"
                f"🏆 Biggest win: <b>${biggest_win_usd:.2f}</b>\n"
                f"🎮 Favorite game: {fav_game_display}"
            )
            await query.edit_message_text(
                stats_text, reply_markup=InlineKeyboardMarkup(stats_kb),
                parse_mode=ParseMode.HTML
            )
            return

        if data == "show_games":
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
                ],
                [
                    InlineKeyboardButton(t("back_button", user_id=user_id), callback_data="back_to_menu"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            sent_show = await query.edit_message_text(
                "🎮 <b>Select a game to play:</b>\n\n"
                "🎲 <b>Dice</b> - Roll the dice and beat the bot!\n"
                "🎳 <b>Bowling</b> - Strike your way to victory!\n"
                "🎯 <b>Darts</b> - Aim for the bullseye!\n"
                "⚽ <b>Football</b> - Score goals and win!\n"
                "🏀 <b>Basketball</b> - Shoot hoops for stars!\n"
                "🪙 <b>Coinflip</b> - Call it and flip! (/cf amount)",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            register_menu_owner(sent_show, user_id)
            return
        
        if data == "play_game_coinflip":
            await query.edit_message_text(
                "🎲 <b>Coinflip</b>\n\n"
                "Use /cf <amount> to play!\n\n"
                "Examples:\n"
                "• /cf 100 — Bet 100 ⭐\n"
                "• /cf all — Bet entire balance\n"
                "• /cf half — Bet half balance",
                parse_mode=ParseMode.HTML
            )
            return
        
        if data.startswith("play_game_"):
            game_type = data.replace("play_game_", "")
            if game_type not in GAME_CONFIG:
                await query.answer(t("err_unknown_game", user_id=user_id), show_alert=True)
                return
            
            if user_id in game_sessions:
                await query.edit_message_text(
                    "❌ You already have an active game! Finish it first.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            balance = get_user_balance(user_id)
            if balance < 1 and not is_admin(user_id):
                await query.edit_message_text(
                    "❌ Insufficient balance! Use /deposit to add Stars.\n"
                    f"Your balance: <b>{balance} ⭐</b>",
                    parse_mode=ParseMode.HTML
                )
                return
            
            config = GAME_CONFIG[game_type]
            context.user_data['game_type'] = game_type
            
            keyboard = [
                [
                    InlineKeyboardButton("10 ⭐", callback_data=f"bet_{game_type}_10"),
                    InlineKeyboardButton("25 ⭐", callback_data=f"bet_{game_type}_25"),
                ],
                [
                    InlineKeyboardButton("50 ⭐", callback_data=f"bet_{game_type}_50"),
                    InlineKeyboardButton("100 ⭐", callback_data=f"bet_{game_type}_100"),
                ],
                [
                    InlineKeyboardButton(t("back_to_games", user_id=user_id), callback_data="show_games"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            sent_pg = await query.edit_message_text(
                f"{config['emoji']} <b>{config['name']}</b>\n\n"
                f"💰 Choose your bet:\n"
                f"Your balance: <b>{balance:,} ⭐</b>",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            register_menu_owner(sent_pg, user_id)
            return

        if data.startswith("demo_game_"):
            if not is_admin(user_id):
                await query.answer(t("err_admin_only_alert", user_id=user_id), show_alert=True)
                return
            
            game_type = data.replace("demo_game_", "")
            if game_type not in GAME_CONFIG:
                await query.answer(t("err_unknown_game", user_id=user_id), show_alert=True)
                return
            
            context.user_data['game_type'] = game_type
            context.user_data['is_demo'] = True
            context.user_data['bet_amount'] = 100  # Demo bet
            
            config = GAME_CONFIG[game_type]
            keyboard = [
                [InlineKeyboardButton(t("mode_normal", user_id=user_id), callback_data=f"mode_normal_{game_type}")],
                [InlineKeyboardButton(t("mode_double", user_id=user_id), callback_data=f"mode_double_{game_type}")],
                [InlineKeyboardButton(t("mode_crazy", user_id=user_id), callback_data=f"mode_crazy_{game_type}")],
                [InlineKeyboardButton(t("back_button", user_id=user_id), callback_data="back_to_demo_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"🎮 <b>DEMO: {config['name']}</b> 🔑\n\n"
                "🎲 <b>Select game mode</b>\n\n"
                "<i>• Normal mode: Highest value wins\n"
                "• Crazy mode: Lowest value wins\n"
                "• Double mode: 2 emojis are rolled in 1 round</i>\n\n"
                "(No Stars will be deducted)",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "back_to_demo_menu":
            keyboard = [
                [
                    InlineKeyboardButton(t("demo_dice_btn", user_id=user_id), callback_data="demo_game_dice"),
                    InlineKeyboardButton(t("demo_bowl_btn", user_id=user_id), callback_data="demo_game_bowl"),
                ],
                [
                    InlineKeyboardButton(t("demo_dart_btn", user_id=user_id), callback_data="demo_game_dart"),
                    InlineKeyboardButton(t("demo_football_btn", user_id=user_id), callback_data="demo_game_football"),
                ],
                [
                    InlineKeyboardButton(t("demo_basketball_btn", user_id=user_id), callback_data="demo_game_basket"),
                ],
                [
                    InlineKeyboardButton(t("btn_cancel_demo", user_id=user_id), callback_data="cancel_demo"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                f"🎮 <b>DEMO MODE</b> 🔑\n\n"
                f"🎯 Choose a game to test:\n"
                f"(No Stars will be deducted)",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "cancel_demo":
            await query.edit_message_text(
                translate_text("❌ Demo cancelled.", user_id=user_id),
                parse_mode=ParseMode.HTML
            )
            return
        
        # ===== NEW POINT-BASED GAME CALLBACKS =====
        
        # Bet selection callback
        if data.startswith("bet_"):
            parts = data.split("_")
            game_type = parts[1]
            bet_amount = int(parts[2])
            
            if game_type not in GAME_CONFIG:
                await query.answer(t("err_unknown_game", user_id=user_id), show_alert=True)
                return
            
            balance = get_user_balance(user_id)
            
            if balance < bet_amount and not is_admin(user_id):
                await query.edit_message_text(
                    "❌ Insufficient balance! Use /deposit to add Stars.",
                    parse_mode=ParseMode.HTML
                )
                return
            
            context.user_data['bet_amount'] = bet_amount
            context.user_data['game_type'] = game_type
            
            config = GAME_CONFIG[game_type]
            keyboard = [
                [InlineKeyboardButton(t("mode_normal", user_id=user_id), callback_data=f"mode_normal_{game_type}")],
                [InlineKeyboardButton(t("mode_double", user_id=user_id), callback_data=f"mode_double_{game_type}")],
                [InlineKeyboardButton(t("mode_crazy", user_id=user_id), callback_data=f"mode_crazy_{game_type}")],
                [InlineKeyboardButton(t("cancel_game", user_id=user_id), callback_data=f"cancel_{game_type}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            sent_bet = await query.edit_message_text(
                "🎲 <b>Select game mode</b>\n\n"
                "<i>• Normal mode: Highest value wins\n"
                "• Crazy mode: Lowest value wins\n"
                "• Double mode: 2 emojis are rolled in 1 round</i>",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            register_menu_owner(sent_bet, user_id)
            return
        
        # Mode selection callback
        if data.startswith("mode_"):
            parts = data.split("_")
            mode = parts[1]  # normal, double, crazy
            game_type = parts[2]
            
            if game_type not in GAME_CONFIG:
                await query.answer(t("err_unknown_game", user_id=user_id), show_alert=True)
                return
            
            context.user_data['mode'] = mode
            config = GAME_CONFIG[game_type]
            
            keyboard = [
                [InlineKeyboardButton(t("btn_up_to_1", user_id=user_id), callback_data=f"points_1_{game_type}")],
                [InlineKeyboardButton(t("btn_up_to_2", user_id=user_id), callback_data=f"points_2_{game_type}")],
                [InlineKeyboardButton(t("btn_up_to_3", user_id=user_id), callback_data=f"points_3_{game_type}")],
                [InlineKeyboardButton("↩ Back", callback_data=f"back_to_mode_{game_type}")],
                [InlineKeyboardButton("🗑 Delete", callback_data=f"cancel_{game_type}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            sent_mode = await query.edit_message_text(
                "🎲 <b>Select the number of points needed to win</b>\n\n"
                "<i>ℹ️ The first player to win the selected number of rounds wins</i>",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            register_menu_owner(sent_mode, user_id)
            return
        
        # Points selection callback
        if data.startswith("points_"):
            parts = data.split("_")
            points_target = int(parts[1])
            game_type = parts[2]
            
            if game_type not in GAME_CONFIG:
                await query.answer(t("err_unknown_game", user_id=user_id), show_alert=True)
                return
            
            bet_amount = context.user_data.get('bet_amount', 10)
            mode = context.user_data.get('mode', 'normal')
            is_demo = context.user_data.get('is_demo', False)
            config = GAME_CONFIG[game_type]
            multiplier = MULTIPLIERS[mode]
            bet_usd = bet_amount * lc.STARS_TO_USD
            
            # Mode descriptions
            mode_display = mode.capitalize()
            if mode == "normal":
                desc = f"the one with the higher {config['action']} wins"
            elif mode == "double":
                desc = f"each player goes twice — highest total wins the round"
            elif mode == "crazy":
                desc = f"the one with the LOWER {config['action']} wins"
            else:
                desc = ""
            
            context.user_data['points_target'] = points_target
            
            demo_tag = " 🔑 DEMO" if is_demo else ""
            
            profile = get_or_create_profile(user_id)
            display_name = profile.get('display_name') or profile.get('username') or 'Player'
            user_link = get_user_link(user_id, display_name)
            
            if is_demo:
                keyboard = [
                    [InlineKeyboardButton("«Accept game»", callback_data=f"play_{game_type}")],
                    [InlineKeyboardButton(t("btn_cancel_game", user_id=user_id), callback_data=f"cancel_{game_type}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                sent_pts = await query.edit_message_text(
                    f"{config['emoji']} <b>{config['name']}</b>{demo_tag}\n\n"
                    f"Bet: ${bet_usd:.2f}\n"
                    f"Multiplier: ×{multiplier}\n"
                    f"Mode: {mode_display} - First to {points_target} point{'s' if points_target > 1 else ''}\n\n"
                    f"<i>To accept the challenge from player {user_link}, click «Accept game» to start PvP</i>",
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
                register_menu_owner(sent_pts, user_id)
                return
                
            # --- CREATE PVP MATCH ---
            import uuid
            import games.pvp as pvp
            
            match_id = str(uuid.uuid4())[:8]
            
            # Lock the creator's bet immediately
            adjust_user_balance(user_id, -bet_amount, game=True)
            
            db.create_pvp_match(
                match_id=match_id,
                game_type=game_type,
                creator_id=user_id,
                creator_name=display_name,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                bet=bet_amount,
                multiplier=multiplier,
                mode=mode,
                target_score=points_target
            )
            
            keyboard = [
                [InlineKeyboardButton("🎲 Accept Game", callback_data=f"pvp_accept_{match_id}")],
                [InlineKeyboardButton("🤖 Play Against Bot", callback_data=f"pvp_bot_{match_id}")],
                [InlineKeyboardButton("❌ Cancel Game", callback_data=f"pvp_cancel_{match_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            text = pvp.build_challenge_message(game_type, bet_amount, mode, points_target, user_id)
            
            sent_pts = await query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            # Do NOT register menu owner, so opponents can click Accept!
            
            # Timeout for challenge is 60s
            context.job_queue.run_once(
                pvp.pvp_timeout_check, 
                60, 
                data={'match_id': match_id},
                name=f"pvp_timeout_{match_id}"
            )
            return
        
        # Replay with same settings from last game
        if data.startswith("replay_"):
            game_type = data.replace("replay_", "")
            if game_type not in GAME_CONFIG:
                await query.answer(t("err_unknown_game", user_id=user_id), show_alert=True)
                return

            if user_id in game_sessions:
                await query.answer(t("err_active_game", user_id=user_id), show_alert=True)
                return

            last = lc.user_last_game_settings.get(user_id)
            if last and last.get('game_type') == game_type:
                bet_amount = last['bet_amount']
                mode = last.get('mode', 'normal')
                points_target = last.get('points_target', 1)
            else:
                bet_amount = 10
                mode = 'normal'
                points_target = 1

            balance = get_user_balance(user_id)
            if balance < bet_amount and not is_admin(user_id):
                await query.answer(f"❌ Insufficient balance! You have {balance} ⭐", show_alert=True)
                return

            await query.answer()

            # Deduct balance
            if not is_admin(user_id):
                adjust_user_balance(user_id, -bet_amount, game=True)
                lc.user_balances[user_id] = get_user_balance(user_id)

            multiplier = MULTIPLIERS[mode]
            config = GAME_CONFIG[game_type]

            game_sessions[user_id] = {
                "game_type": game_type,
                "mode": mode,
                "points_target": points_target,
                "player_score": 0,
                "bot_score": 0,
                "bet": bet_amount,
                "multiplier": multiplier,
                "chat_id": query.message.chat_id,
                "message_id": query.message.message_id,
                "is_demo": False,
                "player_rolls_needed": 2 if mode == "double" else 1,
                "player_rolls_done": 0,
                "player_total": 0,
                "waiting_for_player": True,
            }

            profile = get_or_create_profile(user_id)
            display_name = profile.get('display_name') or profile.get('username') or 'Player'
            user_link = get_user_link(user_id, display_name)
            bet_usd = bet_amount * lc.STARS_TO_USD
            payout_usd = bet_usd * multiplier

            mode_display = mode.capitalize()
            if mode == "normal": mode_display = "Normal"
            elif mode == "double": mode_display = "Double"
            elif mode == "crazy": mode_display = "Crazy"

            await query.edit_message_text(
                f"🔹 The game has started\n\n"
                f"Player 1: {user_link}\n"
                f"Player 2: 🤖 Librate Game\n"
                f"Bet: ${bet_usd:.2f}\n"
                f"Mode: {mode_display} - {points_target} points\n\n"
                f"Roll the dice {config['emoji']}",
                parse_mode=ParseMode.HTML,
                reply_markup=build_copy_turn_reply_markup(user_id, config['emoji'])
            )
            return

        # Double bet replay callback
        if data.startswith("double_"):
            game_type = data.replace("double_", "")
            if game_type not in GAME_CONFIG:
                await query.answer(t("err_unknown_game", user_id=user_id), show_alert=True)
                return

            if user_id in game_sessions:
                await query.answer(t("err_active_game", user_id=user_id), show_alert=True)
                return

            last = lc.user_last_game_settings.get(user_id)
            if last and last.get('game_type') == game_type:
                bet_amount = last['bet_amount'] * 2
                mode = last.get('mode', 'normal')
                points_target = last.get('points_target', 1)
            else:
                bet_amount = 20
                mode = 'normal'
                points_target = 1

            balance = get_user_balance(user_id)
            if balance < bet_amount and not is_admin(user_id):
                await query.answer(f"❌ Insufficient balance! You have {balance} ⭐", show_alert=True)
                return

            await query.answer()

            # Deduct balance
            if not is_admin(user_id):
                adjust_user_balance(user_id, -bet_amount, game=True)
                lc.user_balances[user_id] = get_user_balance(user_id)

            multiplier = MULTIPLIERS[mode]
            config = GAME_CONFIG[game_type]

            game_sessions[user_id] = {
                "game_type": game_type,
                "mode": mode,
                "points_target": points_target,
                "player_score": 0,
                "bot_score": 0,
                "bet": bet_amount,
                "multiplier": multiplier,
                "chat_id": query.message.chat_id,
                "message_id": query.message.message_id,
                "is_demo": False,
                "player_rolls_needed": 2 if mode == "double" else 1,
                "player_rolls_done": 0,
                "player_total": 0,
                "waiting_for_player": True,
            }

            profile = get_or_create_profile(user_id)
            display_name = profile.get('display_name') or profile.get('username') or 'Player'
            user_link = get_user_link(user_id, display_name)
            bet_usd = bet_amount * lc.STARS_TO_USD

            mode_display = mode.capitalize()
            if mode == "normal": mode_display = "Normal"
            elif mode == "double": mode_display = "Double"
            elif mode == "crazy": mode_display = "Crazy"

            await query.edit_message_text(
                f"🔹 The game has started\n\n"
                f"Player 1: {user_link}\n"
                f"Player 2: 🤖 Librate Game\n"
                f"Bet: ${bet_usd:.2f}\n"
                f"Mode: {mode_display} - {points_target} points\n\n"
                f"Roll the dice {config['emoji']}",
                parse_mode=ParseMode.HTML,
                reply_markup=build_copy_turn_reply_markup(user_id, config['emoji'])
            )
            return

        # Play button callback - starts the actual game
        if data.startswith("play_") and not data.startswith("play_game_"):
            game_type = data.replace("play_", "")
            bet_amount = context.user_data.get('bet_amount', 10)
            mode = context.user_data.get('mode', 'normal')
            points_target = context.user_data.get('points_target', 1)
            is_demo = context.user_data.get('is_demo', False)
            await start_bot_game(query, context, user_id, game_type, bet_amount, mode, points_target, is_demo)
            return
        
        # ---- COINFLIP CALLBACKS ----
        if data == "cf_cancel_challenge":
            current_jobs = context.job_queue.get_jobs_by_name(f"cf_timeout_{query.message.message_id}")
            for job in current_jobs:
                job.schedule_removal()
            try:
                await query.message.delete()
            except Exception:
                pass
            return

        if data == "cf_delete_msg":
            try:
                await query.message.delete()
            except Exception:
                pass
            return

        if data == "cf_change_bet":
            try:
                await query.message.delete()
            except Exception:
                pass
            use_stars = context.user_data.get('cf_use_stars', False)
            balance = get_user_balance(user_id)
            text, markup = get_cf_menu(user_id, balance, use_stars)
            sent = await context.bot.send_message(chat_id=query.message.chat_id, text=text, reply_markup=markup, parse_mode="HTML")
            register_menu_owner(sent, user_id)
            return

        if data == "cf_play_bot":
            current_jobs = context.job_queue.get_jobs_by_name(f"cf_timeout_{query.message.message_id}")
            for job in current_jobs:
                job.schedule_removal()
            try:
                await query.message.delete()
            except Exception:
                pass
            bet_amount = context.user_data.get('cf_bet', 10)
            bet_usd = bet_amount * lc.STARS_TO_USD
            balance = get_user_balance(user_id)
            balance_usd = balance * lc.STARS_TO_USD
            text = (
                f"🃏 Make your choice\n\n"
                f"💵 Bet: ${bet_usd:.2f}\n"
                f"🔵 Current balance: ${balance_usd:.2f}"
            )
            keyboard = [
                [
                    InlineKeyboardButton("Heads", callback_data="cf_heads"),
                    InlineKeyboardButton("Tails", callback_data="cf_tails")
                ],
                [InlineKeyboardButton("🗑️  Delete", callback_data="cf_delete_msg")]
            ]
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        if data in ("cf_heads", "cf_tails"):
            try:
                await query.message.delete()
            except Exception:
                pass
            call = "heads" if data == "cf_heads" else "tails"
            bet_amount = context.user_data.get('cf_bet', 10)
            bet_usd = bet_amount * lc.STARS_TO_USD
            payout_usd = bet_amount * CF_MULTIPLIER * lc.STARS_TO_USD
            balance = get_user_balance(user_id)
            if balance < bet_amount:
                await context.bot.send_message(query.message.chat_id, f"❌ Insufficient balance! You need {bet_amount} ⭐")
                return
            adjust_user_balance(user_id, -bet_amount, game=True)
            import random
            outcome = random.choice(["heads", "tails"])
            outcome_emoji = "🌝" if outcome == "heads" else "🌚"
            player_won = (outcome == call)
            sticker_id = coinflip_stickers.get(outcome)
            if sticker_id:
                await context.bot.send_sticker(chat_id=query.message.chat_id, sticker=sticker_id)
                import asyncio
                await asyncio.sleep(2)
            else:
                await context.bot.send_message(chat_id=query.message.chat_id, text=f"Coin result: {outcome_emoji}")
                import asyncio
                await asyncio.sleep(1)
            if player_won:
                winnings_int = int(bet_amount * CF_MULTIPLIER)
                paid = adjust_user_balance(user_id, winnings_int, game=True)
                lc.user_balances[user_id] = get_user_balance(user_id)
                update_game_stats(user_id, 'coinflip', bet_amount, winnings_int, True)
                win_loss_line = f"🏆 Win: ${payout_usd:.2f}"
            else:
                lc.user_balances[user_id] = get_user_balance(user_id)
                update_game_stats(user_id, 'coinflip', bet_amount, 0, False)
                win_loss_line = f"💀 Loss: ${bet_usd:.2f}"
            new_balance_usd = lc.user_balances[user_id] * lc.STARS_TO_USD
            result_text = (
                f"🪙 Bet: ${bet_usd:.2f}\n\n"
                f"History: {'Heads' if outcome == 'heads' else 'Tails'}\n\n"
                f"{win_loss_line}\n"
                f"🔵 Current balance: ${new_balance_usd:.2f}"
            )
            keyboard = [
                [
                    InlineKeyboardButton("🔄 Repeat", callback_data="cf_play_bot"),
                    InlineKeyboardButton("📝 Change bet", callback_data="cf_change_bet")
                ]
            ]
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=result_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return


        
        # Cashout button callback — end game early, return partial bet
        if data.startswith("cashout_"):
            game_type = data.replace("cashout_", "")
            
            if user_id not in game_sessions:
                await query.answer(t("err_no_active_game", user_id=user_id), show_alert=True)
                return
            
            session = game_sessions[user_id]
            if session['game_type'] != game_type:
                await query.answer(t("err_game_mismatch", user_id=user_id), show_alert=True)
                return
            
            config = GAME_CONFIG[game_type]
            bet = session['bet']
            target = session['points_target']
            b_score = session['bot_score']
            p_score = session['player_score']
            is_demo = session.get('is_demo', False)
            
            # Calculate cashout amount
            cashout_stars = int(bet * (target - b_score) / target)
            if cashout_stars < 1:
                cashout_stars = 1
            cashout_usd = cashout_stars * lc.STARS_TO_USD
            
            # Credit cashout to user
            if not is_demo and not is_admin(user_id):
                adjust_user_balance(user_id, cashout_stars, game=True)
                lc.user_balances[user_id] = get_user_balance(user_id)
            
            # Record stats
            if not is_demo:
                stats_game_type = 'arrow' if game_type == 'dart' else game_type
                update_game_stats(user_id, stats_game_type, bet, cashout_stars, cashout_stars > bet)
            
            # Get user display
            profile = get_or_create_profile(user_id)
            display_name = profile.get('display_name') or profile.get('username') or 'Player'
            user_link = get_user_link(user_id, display_name)
            
            # Clean up session
            del game_sessions[user_id]
            
            balance = get_user_balance(user_id)
            
            await query.edit_message_text(
                f"💸 <b>{display_name} cashed out!</b>\n\n"
                f"<b>Scores:</b>\n"
                f"👤 Bot • <b>{b_score}</b>\n"
                f"👤 {user_link} • <b>{p_score}</b>\n\n"
                f"💸 <b>{display_name}</b> cashes out and receives <b>${cashout_usd:.2f}</b>\n\n"
                f"💰 Balance: <b>{balance:,} ⭐</b>",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Cancel game callback
        if data.startswith("cancel_"):
            cancel_game_type = data.replace("cancel_", "")
            
            if user_id in game_sessions:
                session = game_sessions[user_id]
                # Refund bet
                if not session.get('is_demo', False) and not is_admin(user_id):
                    adjust_user_balance(user_id, session['bet'])
                    lc.user_balances[user_id] = get_user_balance(user_id)
                del game_sessions[user_id]
            
            await query.edit_message_text(
                translate_text("❌ Game cancelled.", user_id=user_id),
                parse_mode=ParseMode.HTML
            )
            return
            
    except Exception as e:
        logger.error(f"Button callback error: {e}", exc_info=True)
        try:
            await query.edit_message_text(
                translate_text("❌ An error occurred. Please try again.", user_id=user_id),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
