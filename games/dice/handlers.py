# -*- coding: utf-8 -*-
"""Dice family: /dice /dart /bowl /football /basket (+ /demo).

All five point-based variants share one flow (GAME_CONFIG drives ranges/emojis;
MULTIPLIERS is 1.92x for all — code is the source of truth). Extracted verbatim
from librate_casino; shared, non-rebound helpers are imported from it, and the
rebound STARS_TO_USD scalar is read live via ``lc.STARS_TO_USD``.
"""

import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import (
    game_locks, game_sessions, user_balances, GAME_CONFIG,
    handle_errors, is_admin, is_banned, is_frozen,
    get_user_balance, translate_text, t, register_menu_owner,
    get_or_create_profile, get_user_link, build_copy_turn_reply_markup,
    adjust_user_balance, update_game_stats, save_last_game_settings, db,
)


def register_handlers(app):
    """Register the dice-family command handlers."""
    from telegram.ext import CommandHandler
    app.add_handler(CommandHandler("dice", dice_game))
    app.add_handler(CommandHandler("dart", dart_game))
    app.add_handler(CommandHandler("arrow", dart_game))  # alias
    app.add_handler(CommandHandler("bowl", bowl_game))
    app.add_handler(CommandHandler("football", football_game))
    app.add_handler(CommandHandler("basket", basket_game))
    app.add_handler(CommandHandler("demo", demo_command))


async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE, game_type: str):
    """Core function for starting a new point-based game"""
    user_id = update.effective_user.id
    
    async with game_locks[user_id]:
        if user_id in game_sessions:
            await update.message.reply_html(
                "❌ You already have an active game! Finish it first."
            )
            return
        
        balance = get_user_balance(user_id)
        
        bet_amount = None
        if context.args and len(context.args) > 0:
            arg = context.args[0].lower()
            if arg == 'all':
                bet_amount = int(balance)
            elif arg == 'half':
                bet_amount = int(balance / 2)
            else:
                try:
                    bet_amount = int(arg)
                except ValueError:
                    await update.message.reply_html(translate_text("❌ Invalid bet amount! Use a number, 'all', or 'half'.", user_id=user_id))
                    return
            
            if bet_amount < 1:
                await update.message.reply_html(translate_text("❌ Bet amount must be at least 1 ⭐", user_id=user_id))
                return
            
            if bet_amount > balance and not is_admin(user_id):
                await update.message.reply_html(
                    f"❌ Insufficient balance!\n"
                    f"Your balance: <b>{balance} ⭐</b>\n"
                    f"Bet amount: <b>{bet_amount} ⭐</b>"
                )
                return
            
            # Store bet, go directly to mode selection
            config = GAME_CONFIG[game_type]
            context.user_data['bet_amount'] = bet_amount
            context.user_data['game_type'] = game_type
            
            keyboard = [
                [InlineKeyboardButton(t("mode_normal", user_id=user_id), callback_data=f"mode_normal_{game_type}")],
                [InlineKeyboardButton(t("mode_double", user_id=user_id), callback_data=f"mode_double_{game_type}")],
                [InlineKeyboardButton(t("mode_crazy", user_id=user_id), callback_data=f"mode_crazy_{game_type}")],
                [InlineKeyboardButton(t("cancel_game", user_id=user_id), callback_data=f"cancel_{game_type}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            sent = await update.message.reply_html(
                "🎲 <b>Select game mode</b>\n\n"
                "<i>• Normal mode: Highest value wins\n"
                "• Crazy mode: Lowest value wins\n"
                "• Double mode: 2 emojis are rolled in 1 round</i>",
                reply_markup=reply_markup
            )
            register_menu_owner(sent, user_id)
            return
        
        if balance < 1 and not is_admin(user_id):
            await update.message.reply_html(
                "❌ Insufficient balance! Use /deposit to add Stars.\n"
                f"Your balance: <b>{balance} ⭐</b>"
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
                InlineKeyboardButton(t("cancel_game", user_id=user_id), callback_data=f"cancel_{game_type}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        sent = await update.message.reply_html(
            f"{config['emoji']} <b>{config['name']}</b>\n\n"
            f"💰 Choose your bet:\n"
            f"Your balance: <b>{balance:,} ⭐</b>",
            reply_markup=reply_markup
        )
        register_menu_owner(sent, user_id)

@handle_errors
async def dice_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, game_type="dice")

@handle_errors
async def dart_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, game_type="dart")

@handle_errors
async def football_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, game_type="football")

@handle_errors
async def basket_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, game_type="basket")

@handle_errors
async def bowl_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_game(update, context, game_type="bowl")

@handle_errors
async def demo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ This command is only for administrators.", user_id=user_id))
        return
    
    if user_id in game_sessions:
        await update.message.reply_html(
            "❌ You already have an active game! Finish it first."
        )
        return
    
    keyboard = [
        [
            InlineKeyboardButton(t("game_dice", user_id=user_id), callback_data="demo_game_dice"),
            InlineKeyboardButton(t("game_bowl", user_id=user_id), callback_data="demo_game_bowl"),
        ],
        [
            InlineKeyboardButton(t("game_dart", user_id=user_id), callback_data="demo_game_dart"),
            InlineKeyboardButton(t("game_football", user_id=user_id), callback_data="demo_game_football"),
        ],
        [
            InlineKeyboardButton(t("game_basketball", user_id=user_id), callback_data="demo_game_basket"),
        ],
        [
            InlineKeyboardButton(t("cancel_demo", user_id=user_id), callback_data="cancel_demo"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(
        f"🎮 <b>DEMO MODE</b> 🔑\n\n"
        f"🎯 Choose a game to test:\n"
        f"(No Stars will be deducted)",
        reply_markup=reply_markup
    )

async def start_round(context, chat_id, user_id):
    """Prepare session for next player roll (user always rolls first)"""
    session = game_sessions.get(user_id)
    if not session:
        return
    mode = session['mode']
    session['player_rolls_needed'] = 2 if mode == "double" else 1
    session['player_rolls_done'] = 0
    session['player_total'] = 0
    session['waiting_for_player'] = True

async def complete_round(context, chat_id, user_id):
    """Compare scores, send round result, continue or end game"""
    session = game_sessions.get(user_id)
    if not session:
        return

    game_type = session['game_type']
    mode = session['mode']

    player_val = session['player_total']
    bot_val = session['bot_value']

    profile = get_or_create_profile(user_id)
    display_name = profile.get('display_name') or profile.get('username') or 'Player'
    user_link = get_user_link(user_id, display_name)
    game_emoji = GAME_CONFIG.get(game_type, {}).get('emoji', '🎲')
    copy_turn_markup = build_copy_turn_reply_markup(user_id, game_emoji)

    # --- TIE ---
    if player_val == bot_val:
        b_score = session['bot_score']
        p_score = session['player_score']
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🤝 It's a tie!\n\n"
                f"Scores:\n"
                f"👤 Bot • {b_score}\n"
                f"👤 {user_link} • {p_score}\n\n"
                f"🎮 Waiting for {display_name}...\n"
                f"👉 Next round: {user_link}, it's your turn."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=copy_turn_markup
        )
        await start_round(context, chat_id, user_id)
        return

    # --- DETERMINE ROUND WINNER ---
    if mode == "crazy":
        player_wins = player_val < bot_val
    else:
        player_wins = player_val > bot_val

    if player_wins:
        session['player_score'] += 1
    else:
        session['bot_score'] += 1

    p_score = session['player_score']
    b_score = session['bot_score']
    target = session['points_target']
    bet = session['bet']
    is_demo = session.get('is_demo', False)
    multiplier = session['multiplier']

    scores_block = (
        f"Scores:\n"
        f"👤 Bot • {b_score}\n"
        f"👤 {user_link} • {p_score}"
    )

    round_header = f"🏆 {display_name} wins this round!" if player_wins else "🏆 Bot wins this round!"
    demo_tag = " 🔑" if is_demo else ""

    # --- GAME OVER ---
    if p_score >= target or b_score >= target:
        bet_usd = bet * lc.STARS_TO_USD
        earned_usd = bet_usd * multiplier

        if p_score >= target:
            winnings_int = int(bet * multiplier)
            if not is_demo:
                paid = adjust_user_balance(user_id, winnings_int, game=True)
                if paid is False:
                    final_line = "🔧 <b>Casino Maintenance</b>\n\nThe casino is temporarily unable to process this win. Please try again shortly."
                else:
                    user_balances[user_id] = get_user_balance(user_id)
                    stats_game_type = 'arrow' if game_type == 'dart' else game_type
                    update_game_stats(user_id, stats_game_type, bet, winnings_int, True)
                    save_last_game_settings(user_id, game_type, bet, mode, target)
                    final_line = f"🎉 {user_link} wins the game and earns ${earned_usd:.2f} {multiplier}x"
            else:
                final_line = f"🎉 {user_link} wins the game and earns ${earned_usd:.2f} {multiplier}x"
        else:
            if not is_demo:
                stats_game_type = 'arrow' if game_type == 'dart' else game_type
                update_game_stats(user_id, stats_game_type, bet, 0, False)
                save_last_game_settings(user_id, game_type, bet, mode, target)
            final_line = "💀 Bot wins the game.\nBetter luck next time!"

        if user_id in game_sessions:
            del game_sessions[user_id]

        game_over_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔁 Repeat", callback_data=f"replay_{game_type}"),
                InlineKeyboardButton("×2 Double", callback_data=f"double_{game_type}"),
            ]
        ])
        
        if p_score >= target:
            msg_text = (
                f"🔹 The game has ended{demo_tag}\n\n"
                f"👑 Winner: {user_link} - {p_score} points\n"
                f"👎 Loser: 🤖 Librate Game - {b_score} points\n"
                f"Win + ${earned_usd:.2f}"
            )
        else:
            msg_text = (
                f"🔹 The game has ended{demo_tag}\n\n"
                f"👑 Winner: 🤖 Librate Game - {b_score} points\n"
                f"👎 Loser: {user_link} - {p_score} points\n"
                f"Loss - ${bet_usd:.2f}"
            )

        await context.bot.send_message(
            chat_id=chat_id,
            text=msg_text,
            parse_mode=ParseMode.HTML,
            reply_markup=game_over_keyboard
        )

    # --- ROUND CONTINUES ---
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"{round_header}\n\n"
                f"{scores_block}\n\n"
                f"🎮 Waiting for {display_name}...\n"
                f"👉 Next round: {user_link}, it's your turn."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=copy_turn_markup
        )
        await start_round(context, chat_id, user_id)

@handle_errors
async def handle_game_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle dice emoji messages from player during their turn"""
    user_id = update.effective_user.id

    if is_banned(user_id):
        return

    if is_frozen(user_id) and not is_admin(user_id):
        return

    chat_id = update.effective_chat.id
    
    # Check if user is in an active PvP match
    pvp_match = db.get_active_pvp_match(user_id, chat_id)
    if pvp_match:
        import games.pvp as pvp
        await pvp.handle_pvp_roll(update, context, pvp_match)
        return

    session = game_sessions.get(user_id)
    if not session or not session.get('waiting_for_player'):
        return

    chat_id = update.effective_chat.id
    game_type = session['game_type']
    config = GAME_CONFIG[game_type]

    dice = update.message.dice
    if dice.emoji != config['tg_emoji']:
        return

    session['player_rolls_done'] += 1
    session['player_total'] += dice.value

    # Double mode needs 2 rolls from the player
    if session['player_rolls_done'] < session['player_rolls_needed']:
        return

    # All player rolls received — now bot rolls
    session['waiting_for_player'] = False

    mode = session['mode']
    if mode == "double":
        b1 = await context.bot.send_dice(chat_id=chat_id, emoji=config['tg_emoji'])
        await asyncio.sleep(2)
        b2 = await context.bot.send_dice(chat_id=chat_id, emoji=config['tg_emoji'])
        await asyncio.sleep(2)
        session['bot_value'] = b1.dice.value + b2.dice.value
    else:
        bot_msg = await context.bot.send_dice(chat_id=chat_id, emoji=config['tg_emoji'])
        await asyncio.sleep(2)
        session['bot_value'] = bot_msg.dice.value

    await complete_round(context, chat_id, user_id)
