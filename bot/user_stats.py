# -*- coding: utf-8 -*-
"""User-facing stats commands: /profile /levels /history /matches /leaderboard
(+ rendering helpers). Lifted verbatim from librate_casino; STARS_TO_USD and
user_game_history (load_data-rebound) read live via lc.*; constants/helpers
imported. Re-imported so main + button_callback (history paging) resolve them.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import (
    CASINO_LEVELS, GAME_CONFIG, GAME_EMOJIS, GAME_NAMES, GAME_TYPES,
    LEADERBOARD_DATA, LEADERBOARD_IMAGES, LEVEL_THRESHOLDS,
    MATCHES_PER_PAGE, MATCH_GAME_DISPLAY, MATCH_ID_BASE,
    create_progress_bar, db, get_or_create_profile, get_user_balance,
    get_user_link, handle_errors, logger, register_menu_owner,
    send_bot_reply_html, start, t, translate_text,
)


def get_user_level(total_bets_usd):
    """Calculate user's level based on total bets in USD"""
    try:
        # Ensure total_bets_usd is a valid number
        if not isinstance(total_bets_usd, (int, float)):
            total_bets_usd = 0.0
        total_bets_usd = max(0.0, float(total_bets_usd))
        
        level = 0
        for lvl, threshold in sorted(LEVEL_THRESHOLDS.items(), reverse=True):
            if total_bets_usd >= threshold:
                level = lvl
                break
        return max(0, min(25, level))
    except Exception as e:
        logger.error(f"Error in get_user_level: {e}", exc_info=True)
        return 0

def get_level_progress(total_bets_usd, current_level):
    """Calculate progress percentage to next level"""
    try:
        # Ensure inputs are valid
        if not isinstance(total_bets_usd, (int, float)):
            total_bets_usd = 0.0
        total_bets_usd = max(0.0, float(total_bets_usd))
        current_level = int(max(0, min(25, current_level)))
        
        if current_level >= 25:  # MAX LEVEL
            return 100
        
        current_threshold = LEVEL_THRESHOLDS.get(current_level, 0)
        next_threshold = LEVEL_THRESHOLDS.get(current_level + 1)
        
        if next_threshold is None or next_threshold == current_threshold:
            return 100
        
        if next_threshold - current_threshold == 0:
            return 100
        
        progress = ((total_bets_usd - current_threshold) / (next_threshold - current_threshold)) * 100
        return max(0, min(100, progress))
    except Exception as e:
        logger.error(f"Error in get_level_progress: {e}", exc_info=True)
        return 0

def format_level_display(user_id, username=None):
    """Format the level display for a user"""
    profile = get_or_create_profile(user_id, username)
    total_bets = profile.get('total_bets', 0.0)
    total_bets_usd = total_bets * lc.STARS_TO_USD
    
    current_level = get_user_level(total_bets_usd)
    # Ensure level is within valid range
    current_level = max(0, min(25, current_level))
    level_info = CASINO_LEVELS.get(current_level, CASINO_LEVELS[0])
    progress = get_level_progress(total_bets_usd, current_level)
    
    # Current level features
    current_rakeback = level_info.get('rakeback', 5.0)
    current_weekly = level_info.get('weekly_mult', 1.09)
    
    # Next level info
    if current_level < 25:
        next_level = current_level + 1
        next_level_info = CASINO_LEVELS.get(next_level)
        if not next_level_info:
            next_level_info = CASINO_LEVELS[25]  # Fallback to max level
        next_rakeback = next_level_info.get('rakeback', current_rakeback)
        next_weekly = next_level_info.get('weekly_mult', current_weekly)
        level_up_bonus = next_level_info.get('level_up_bonus', 0)
        next_level_name = next_level_info.get('name', 'MAX LEVEL')
    else:
        next_level = None
        next_level_name = "MAX LEVEL"
        next_rakeback = current_rakeback
        next_weekly = current_weekly
        level_up_bonus = 0
    
    progress_bar = create_progress_bar(progress)
    
    text = f"Your profile Level: <b>{level_info.get('name', 'Steel')} (Lvl {current_level})</b>\n"
    text += f"Progress: <b>{progress:.1f}%</b> → {next_level_name}\n"
    text += f"{progress_bar}\n\n"
    
    text += f"<b>[{level_info.get('name', 'Steel')}] features:</b>\n"
    text += f"Rakeback: <b>{current_rakeback}%</b>\n"
    text += f"Weekly bonus: <b>{current_weekly}x</b>\n\n"
    
    if current_level < 25:
        text += f"<b>[{next_level_name}] features:</b>\n"
        text += f"Level-Up bonus: <b>${level_up_bonus}</b>\n"
        text += f"Rakeback: <b>{current_rakeback}%</b> → <b>{next_rakeback}%</b>\n"
        text += f"Weekly bonus: <b>{current_weekly}x</b> → <b>{next_weekly}x</b>\n"
    
    return text

@handle_errors
async def levels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's level and all available levels"""
    try:
        user = update.effective_user
        user_id = user.id
        
        profile = get_or_create_profile(user_id, user.username or user.first_name)
        total_bets = profile.get('total_bets', 0.0)
        
        # Ensure total_bets is a valid number
        try:
            total_bets = float(total_bets) if total_bets else 0.0
        except (ValueError, TypeError):
            total_bets = 0.0
        
        total_bets_usd = total_bets * lc.STARS_TO_USD
        
        # Initialize all variables with defaults
        current_level = 0
        level_info = CASINO_LEVELS[0]
        progress = 0.0
        current_rakeback = 5.0
        current_weekly = 1.09
        next_rakeback = 6.5
        next_weekly = 1.09
        level_up_bonus = 5
        next_level_name = "Iron I"
        level_name = "Steel"
        
        try:
            current_level = get_user_level(total_bets_usd)
            # Ensure level is within valid range
            current_level = max(0, min(25, int(current_level)))
            level_info = CASINO_LEVELS.get(current_level)
            if not level_info:
                level_info = CASINO_LEVELS[0]
            
            progress = get_level_progress(total_bets_usd, current_level)
            if progress is None:
                progress = 0.0
            progress = float(progress)
            
            # Current level features
            current_rakeback = float(level_info.get('rakeback', 5.0))
            current_weekly = float(level_info.get('weekly_mult', 1.09))
            level_name = str(level_info.get('name', 'Steel'))
            
            # Next level info
            if current_level < 25:
                next_level = current_level + 1
                next_level_info = CASINO_LEVELS.get(next_level)
                if next_level_info:
                    next_rakeback = float(next_level_info.get('rakeback', current_rakeback))
                    next_weekly = float(next_level_info.get('weekly_mult', current_weekly))
                    level_up_bonus = int(next_level_info.get('level_up_bonus', 0))
                    next_level_name = str(next_level_info.get('name', 'MAX LEVEL'))
                else:
                    next_level_name = "MAX LEVEL"
                    next_rakeback = current_rakeback
                    next_weekly = current_weekly
                    level_up_bonus = 0
            else:
                next_level_name = "MAX LEVEL"
                next_rakeback = current_rakeback
                next_weekly = current_weekly
                level_up_bonus = 0
        except Exception as e:
            logger.error(f"Error calculating level info: {e}", exc_info=True)
            # Use defaults already set above
        
        try:
            progress_bar = create_progress_bar(progress)
            if not progress_bar:
                progress_bar = "▱" * 20
        except Exception:
            progress_bar = "▱" * 20
        
        # Build the message text
        try:
            text = f"Your profile Level: <b>{level_name} (Lvl {current_level})</b>\n"
            text += f"Progress: <b>{progress:.1f}%</b> → {next_level_name}\n"
            text += f"{progress_bar}\n\n"
            
            text += f"<b>[{level_name}] features:</b>\n"
            text += f"Rakeback: <b>{current_rakeback}%</b>\n"
            text += f"Weekly bonus: <b>{current_weekly}x</b>\n\n"
            
            if current_level < 25:
                text += f"<b>[{next_level_name}] features:</b>\n"
                text += f"Level-Up bonus: <b>${level_up_bonus}</b>\n"
                text += f"Rakeback: <b>{current_rakeback}%</b> → <b>{next_rakeback}%</b>\n"
                text += f"Weekly bonus: <b>{current_weekly}x</b> → <b>{next_weekly}x</b>\n\n"
            
            text += "Use /levels to see all the rank levels, benefits and bonuses"
            
            await update.message.reply_html(text)
        except Exception as e:
            logger.error(f"Error formatting level text: {e}", exc_info=True)
            raise
    except Exception as e:
        logger.error(f"Error in levels_command: {e}", exc_info=True)
        await update.message.reply_html(
            translate_text(
                "❌ <b>An error occurred while displaying your level.</b>\n\n"
                "Please try again later."
            )
        )

@handle_errors
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    profile = get_or_create_profile(user_id, user.username or user.first_name)
    balance = get_user_balance(user_id)
    balance_usd = balance * lc.STARS_TO_USD
    
    user_link = get_user_link(user_id, user.first_name)
    
    # Favorite game (dynamically calculated)
    fav_game = profile.get('favorite_game')
    if fav_game and fav_game in GAME_TYPES:
        fav_game_display = f"{GAME_TYPES[fav_game]['icon']} {GAME_TYPES[fav_game]['name']}"
    elif fav_game and fav_game in GAME_CONFIG:
        fav_game_display = f"{GAME_CONFIG[fav_game]['emoji']} {GAME_CONFIG[fav_game]['name']}"
    else:
        fav_game_display = "None"
    
    # Biggest win
    biggest_win = profile.get('biggest_win', 0)
    biggest_win_usd = biggest_win * lc.STARS_TO_USD if biggest_win > 0 else 0.0
    
    # Registration date (DD.MM.YYYY format)
    reg_date = profile.get('registration_date', datetime.now())
    reg_date_str = reg_date.strftime("%d.%m.%Y")
    
    # Total bets and wins in USD
    try:
        total_bets = float(profile.get('total_bets', 0) or 0)
    except (ValueError, TypeError):
        total_bets = 0.0
    
    try:
        total_wins = float(profile.get('total_wins', 0) or 0)
    except (ValueError, TypeError):
        total_wins = 0.0
    
    total_bets_usd = total_bets * lc.STARS_TO_USD
    total_wins_usd = total_wins * lc.STARS_TO_USD
    
    # Rank from level system
    try:
        current_level = get_user_level(total_bets_usd)
        current_level = max(0, min(25, current_level))
        level_info = CASINO_LEVELS.get(current_level, CASINO_LEVELS[0])
        rank_name = level_info.get('name', 'Steel')
    except Exception:
        rank_name = "Steel"
    
    total_games = profile.get('total_games', 0)
    
    profile_text = (
        f"👤 <b>Profile</b>\n\n"
        f"â¹ï¸  User: {user_link} (<code>{user_id}</code>)\n"
        f"🏅 Rank: {rank_name}\n"
        f"💰 Balance: <b>${balance_usd:.2f}</b>\n\n"
        f"⚡ Total games: <b>{total_games}</b>\n"
        f"Total bet amount: <b>${total_bets_usd:.2f}</b>\n"
        f"Total winnings: <b>${total_wins_usd:.2f}</b>\n\n"
        f"🎲 Favorite game: {fav_game_display}\n"
        f"🎉 Biggest win: <b>${biggest_win_usd:.2f}</b>\n\n"
        f"🕒 Registration date: {reg_date_str}"
    )
    
    await send_bot_reply_html(
        update.message, profile_text, message_key="profile",
        chat_id=update.effective_chat.id
    )

async def send_or_edit_history(update_or_query, user_id, page=1):
    history = db.get_game_history(user_id, limit=9999) # Fetch recent max
    total_entries = len(history)
    items_per_page = 5
    total_pages = max(1, (total_entries + items_per_page - 1) // items_per_page)
    
    if page < 1: page = 1
    if page > total_pages: page = total_pages
    
    start_idx = (page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    
    page_items = history[start_idx:end_idx]
    
    text = "📋 <b>Game history</b>\n\n"
    
    for item in page_items:
        game_id = item['id']
        gtype = item['game_type']
        bet = item['bet_amount'] * lc.STARS_TO_USD
        win = item['win_amount'] * lc.STARS_TO_USD
        if not item['won']:
            win = 0.00
            
        emoji = GAME_EMOJIS.get(gtype, "🎮")
        name = GAME_NAMES.get(gtype, gtype.title())
        
        dt = item['timestamp']
        if isinstance(dt, str):
            try:
                dt = datetime.fromisoformat(dt)
            except:
                pass
                
        if hasattr(dt, 'strftime'):
            dt_formatted = dt.strftime("%d.%m.%Y %H:%M")
        else:
            dt_formatted = str(dt)[:16].replace("-", ".")
            
        text += f'<blockquote expandable>{emoji} {name} #{game_id} | {dt_formatted}\n'
        text += f'💵 Bet: ${bet:.2f}\n'
        text += f'👑 Win: ${win:.2f}</blockquote>\n\n'
        
    text += f"Page {page}/{total_pages}"
    
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("←", callback_data=f"history_page_{page-1}"))
    else:
        nav_buttons.append(InlineKeyboardButton("←", callback_data="ignore"))
        
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("→", callback_data=f"history_page_{page+1}"))
    else:
        nav_buttons.append(InlineKeyboardButton("→", callback_data="ignore"))
        
    keyboard = []
    if nav_buttons:
        keyboard.append(nav_buttons)
        
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="close_history")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if hasattr(update_or_query, 'edit_message_text'):
        await update_or_query.edit_message_text(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update_or_query.reply_html(text, reply_markup=reply_markup)

@handle_errors
async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await send_or_edit_history(update.message, user_id, page=1)

def format_matches_page(history_list, page, total_pages):
    """Format a single page of match history matching screenshot style."""
    start = page * MATCHES_PER_PAGE
    end = start + MATCHES_PER_PAGE
    page_entries = history_list[start:end]

    if not page_entries:
        return "📋 <b>Game history</b>\n\nNo matches found."

    text = "📋 <b>Game history</b>\n"

    for entry in page_entries:
        game_type = entry.get('game_type', 'unknown')
        display = MATCH_GAME_DISPLAY.get(game_type, {'emoji': '🎮', 'name': game_type.title()})
        emoji = display['emoji']
        name = display['name']

        match_id = entry.get('match_id', 0)

        ts = entry.get('timestamp')
        if isinstance(ts, datetime):
            ts_str = ts.strftime("%d.%m.%Y  %H:%M")
        elif isinstance(ts, str):
            try:
                ts_str = datetime.fromisoformat(ts).strftime("%d.%m.%Y  %H:%M")
            except Exception:
                ts_str = ts
        else:
            ts_str = "—"

        bet_usd = entry.get('bet_amount', 0) * lc.STARS_TO_USD
        win_usd = entry.get('win_amount', 0) * lc.STARS_TO_USD

        text += (
            f"\n{emoji} {name} #{match_id} | {ts_str}\n"
            f"💰 Bet: <b>${bet_usd:.2f}</b>\n"
            f"👑 Win: <b>${win_usd:.2f}</b>\n"
        )

    text += f"\nPage {page + 1}/{total_pages}"
    return text

@handle_errors
async def matches_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paginated game history - /matches"""
    user = update.effective_user
    user_id = user.id

    get_or_create_profile(user_id, user.username or user.first_name)
    history = lc.user_game_history.get(user_id, [])

    if not history:
        await update.message.reply_html(
            "📋 <b>Game history</b>\n\n"
            "No matches yet. Play a game to see your history!"
        )
        return

    # Build reversed list (newest first) with match IDs
    total = len(history)
    history_reversed = []
    for i, entry in enumerate(reversed(history)):
        entry_copy = dict(entry)
        entry_copy['match_id'] = MATCH_ID_BASE + total - i
        history_reversed.append(entry_copy)

    total_pages = max(1, (len(history_reversed) + MATCHES_PER_PAGE - 1) // MATCHES_PER_PAGE)
    page = 0

    text = format_matches_page(history_reversed, page, total_pages)

    # Build pagination buttons
    buttons = []
    if total_pages > 1:
        buttons.append(InlineKeyboardButton("âž¡ï¸¯¸", callback_data=f"matches_page_{page + 1}"))
    keyboard = [buttons] if buttons else []
    keyboard.append([InlineKeyboardButton(t("back_button", user_id=user_id), callback_data="matches_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    sent = await update.message.reply_html(text, reply_markup=reply_markup)
    register_menu_owner(sent, user_id)

def _build_lb_caption(category):
    """Build a formatted leaderboard caption for the given category."""
    data = LEADERBOARD_DATA[category]
    lines = [f"<b>{data['title']}</b>\n"]
    for rank, name, value in data["entries"]:
        lines.append(f"{rank} <b>{name}</b> — {value}")
    return "\n".join(lines)

def _build_lb_keyboard():
    """Build the 2x2 inline keyboard for leaderboard categories."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏆 Most Wins", callback_data="lb_wins"),
            InlineKeyboardButton("💰 Most Money Won", callback_data="lb_money"),
        ],
        [
            InlineKeyboardButton("🎮 Most Active", callback_data="lb_active"),
            InlineKeyboardButton("🎲 Highest Roller", callback_data="lb_roller"),
        ],
    ])

@handle_errors
async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show leaderboard with photo and category filter buttons."""
    user = update.effective_user
    get_or_create_profile(user.id, user.username or user.first_name)

    caption = _build_lb_caption("wins")
    markup = _build_lb_keyboard()

    with open(LEADERBOARD_IMAGES["wins"], "rb") as img:
        sent = await update.message.reply_photo(
            photo=img,
            caption=caption,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
        )
    register_menu_owner(sent, user.id)
