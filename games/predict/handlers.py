# -*- coding: utf-8 -*-
"""Predict game handlers: /predict command + all pred_* callbacks.

Lifted verbatim from librate_casino. Engine (predict_get_multiplier) lives in
games.predict.engine; shared non-rebound helpers/state imported from
librate_casino; rebound STARS_TO_USD read live via lc.STARS_TO_USD. Re-imported
into librate_casino so button_callback (pred_ delegation) and main resolve them.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import (
    t, handle_errors, is_banned, is_admin, get_user_balance,
    predict_sessions, game_sessions, register_menu_owner,
    update_game_stats, adjust_user_balance,
    PREDICT_DEFAULT_BET, PREDICT_MIN_BET,
)
from games.predict.engine import predict_get_multiplier


def predict_build_message(user_id, session):
    """Build the predict game message text"""
    selected = session.get('selected', set())
    selection_type = session.get('selection_type')
    bet = session.get('bet', PREDICT_DEFAULT_BET)
    balance = get_user_balance(user_id)
    balance_usd = balance * lc.STARS_TO_USD

    mult = predict_get_multiplier(selected, selection_type)

    # Format selected display
    if selection_type == "even":
        sel_display = "Even (2, 4, 6)"
    elif selection_type == "odd":
        sel_display = "Odd (1, 3, 5)"
    elif selection_type == "low":
        sel_display = "1-3"
    elif selection_type == "high":
        sel_display = "4-6"
    elif selected:
        sel_display = " ".join(str(n) for n in sorted(selected))
    else:
        sel_display = "None"

    bet_usd = bet * lc.STARS_TO_USD

    text = (
        f"🎲 <b>Make a prediction for number outcomes</b>\n\n"
        f"🔵 Multiplier: <b>x{mult:.2f}</b>\n"
        f"🔥 Selected numbers: <b>{sel_display}</b>\n"
        f"💰 Bet: <b>${bet_usd:.2f}</b> ({bet} ⭐)\n"
        f"🧿 Current balance: <b>${balance_usd:.2f}</b> ({balance:,} ⭐)"
    )
    return text


def predict_build_keyboard(session, user_id=None):
    """Build the predict game inline keyboard"""
    selected = session.get('selected', set())
    selection_type = session.get('selection_type')

    def num_label(n):
        if selection_type == "even" and n % 2 == 0:
            return f"✅ {n}"
        elif selection_type == "odd" and n % 2 == 1:
            return f"✅ {n}"
        elif selection_type == "low" and n <= 3:
            return f"✅ {n}"
        elif selection_type == "high" and n >= 4:
            return f"✅ {n}"
        elif n in selected and selection_type is None:
            return f"✅ {n}"
        return str(n)

    keyboard = [
        [
            InlineKeyboardButton(num_label(1), callback_data="pred_num_1"),
            InlineKeyboardButton(num_label(2), callback_data="pred_num_2"),
            InlineKeyboardButton(num_label(3), callback_data="pred_num_3"),
        ],
        [
            InlineKeyboardButton(num_label(4), callback_data="pred_num_4"),
            InlineKeyboardButton(num_label(5), callback_data="pred_num_5"),
            InlineKeyboardButton(num_label(6), callback_data="pred_num_6"),
        ],
        [
            InlineKeyboardButton(("✅ " if selection_type == "even" else "") + t("btn_even", user_id=user_id), callback_data="pred_even"),
            InlineKeyboardButton(("✅ " if selection_type == "odd" else "") + t("btn_odd", user_id=user_id), callback_data="pred_odd"),
        ],
        [
            InlineKeyboardButton("✅ 1-3" if selection_type == "low" else "1-3", callback_data="pred_low"),
            InlineKeyboardButton("✅ 4-6" if selection_type == "high" else "4-6", callback_data="pred_high"),
        ],
        [
            InlineKeyboardButton(t("btn_play", user_id=user_id), callback_data="pred_play"),
        ],
        [
            InlineKeyboardButton(t("btn_change_bet", user_id=user_id), callback_data="pred_change_bet"),
        ],
        [
            InlineKeyboardButton(t("btn_cancel_game2", user_id=user_id), callback_data="pred_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


@handle_errors
async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /predict command"""
    user_id = update.effective_user.id

    if is_banned(user_id) and not is_admin(user_id):
        return

    if user_id in predict_sessions:
        await update.message.reply_html(t("pred_active", user_id=user_id))
        return

    if user_id in game_sessions:
        await update.message.reply_html(t("active_game", user_id=user_id))
        return

    balance = get_user_balance(user_id)

    # Parse bet from args
    bet = PREDICT_DEFAULT_BET
    if context.args and len(context.args) > 0:
        arg = context.args[0].lower()
        if arg == 'all':
            bet = int(balance)
        elif arg == 'half':
            bet = int(balance / 2)
        else:
            try:
                bet = int(arg)
            except ValueError:
                await update.message.reply_html(t("invalid_bet_amount", user_id=user_id))
                return

    if bet < PREDICT_MIN_BET:
        bet = PREDICT_MIN_BET

    if balance < bet and not is_admin(user_id):
        await update.message.reply_html(
            f"❌ Insufficient balance!\n"
            f"Your balance: <b>{balance} ⭐</b>\n"
            f"Minimum bet: <b>{PREDICT_MIN_BET} ⭐</b>"
        )
        return

    session = {
        'chat_id': update.effective_chat.id,
        'message_id': None,
        'selected': set(),
        'selection_type': None,
        'bet': bet,
    }
    predict_sessions[user_id] = session

    text = predict_build_message(user_id, session)
    keyboard = predict_build_keyboard(session, user_id=user_id)

    sent = await update.message.reply_html(text, reply_markup=keyboard)
    session['message_id'] = sent.message_id
    register_menu_owner(sent, user_id)


async def handle_predict_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all predict game callbacks"""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    session = predict_sessions.get(user_id)
    if not session:
        await query.answer(t("err_no_predict", user_id=user_id), show_alert=True)
        return

    # --- Number toggle ---
    if data.startswith("pred_num_"):
        num = int(data.split("_")[-1])
        # If a special selection type was active, switch to manual
        if session['selection_type'] is not None:
            session['selection_type'] = None
            session['selected'] = set()

        if num in session['selected']:
            session['selected'].discard(num)
        else:
            if len(session['selected']) >= 5:
                await query.answer(t("err_max_5_nums", user_id=user_id), show_alert=True)
                return
            session['selected'].add(num)

        # Block selecting all 6
        if session['selected'] == {1, 2, 3, 4, 5, 6}:
            session['selected'].discard(num)
            await query.answer(t("err_cant_all_6", user_id=user_id), show_alert=True)
            return

        text = predict_build_message(user_id, session)
        keyboard = predict_build_keyboard(session, user_id=user_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await query.answer()
        return

    # --- Even / Odd / Low / High ---
    if data in ("pred_even", "pred_odd", "pred_low", "pred_high"):
        type_map = {"pred_even": "even", "pred_odd": "odd", "pred_low": "low", "pred_high": "high"}
        new_type = type_map[data]
        if session['selection_type'] == new_type:
            session['selection_type'] = None
            session['selected'] = set()
        else:
            session['selection_type'] = new_type
            session['selected'] = set()

        text = predict_build_message(user_id, session)
        keyboard = predict_build_keyboard(session, user_id=user_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await query.answer()
        return

    # --- Change Bet ---
    if data == "pred_change_bet":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("5 ⭐", callback_data="pred_bet_5"),
                InlineKeyboardButton("10 ⭐", callback_data="pred_bet_10"),
                InlineKeyboardButton("25 ⭐", callback_data="pred_bet_25"),
            ],
            [
                InlineKeyboardButton("50 ⭐", callback_data="pred_bet_50"),
                InlineKeyboardButton("100 ⭐", callback_data="pred_bet_100"),
                InlineKeyboardButton(t("btn_all_in", user_id=user_id), callback_data="pred_bet_all"),
            ],
            [
                InlineKeyboardButton(t("back_button", user_id=user_id), callback_data="pred_bet_back"),
            ],
        ])
        await query.edit_message_text(
            "📝 <b>Change your bet amount:</b>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        await query.answer()
        return

    if data.startswith("pred_bet_"):
        bet_val = data.replace("pred_bet_", "")
        if bet_val == "back":
            text = predict_build_message(user_id, session)
            keyboard = predict_build_keyboard(session, user_id=user_id)
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            await query.answer()
            return

        balance = get_user_balance(user_id)
        if bet_val == "all":
            new_bet = int(balance)
        else:
            new_bet = int(bet_val)

        if new_bet < PREDICT_MIN_BET:
            new_bet = PREDICT_MIN_BET

        if new_bet > balance and not is_admin(user_id):
            await query.answer(f"❌ Insufficient balance! You have {balance} ⭐", show_alert=True)
            return

        session['bet'] = new_bet
        text = predict_build_message(user_id, session)
        keyboard = predict_build_keyboard(session, user_id=user_id)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await query.answer()
        return

    # --- Cancel ---
    if data == "pred_cancel":
        del predict_sessions[user_id]
        await query.edit_message_text("🔴 Predict game cancelled.", parse_mode=ParseMode.HTML)
        await query.answer()
        return

    # --- Play ---
    if data == "pred_play":
        selected = session['selected']
        selection_type = session['selection_type']

        # Validate selection
        has_selection = bool(selected) or selection_type is not None
        if not has_selection:
            await query.answer(t("err_select_at_least_one", user_id=user_id), show_alert=True)
            return

        bet = session['bet']
        balance = get_user_balance(user_id)

        if bet > balance and not is_admin(user_id):
            await query.answer(f"❌ Insufficient balance! You have {balance} ⭐", show_alert=True)
            return

        # Deduct bet
        adjust_user_balance(user_id, -bet, game=True)

        profile = get_or_create_profile(user_id)
        display_name = profile.get('display_name') or profile.get('username') or 'Player'
        user_link = get_user_link(user_id, display_name)

        # Show rolling message
        await query.edit_message_text(
            f"🎲 Rolling dice for {user_link}...",
            parse_mode=ParseMode.HTML
        )

        # Send dice animation
        chat_id = session['chat_id']
        dice_msg = await context.bot.send_dice(chat_id=chat_id, emoji="🎲")
        dice_value = dice_msg.dice.value

        # Wait for animation
        await asyncio.sleep(2.5)

        # Determine win/loss
        winning_numbers = set()
        if selection_type == "even":
            winning_numbers = {2, 4, 6}
        elif selection_type == "odd":
            winning_numbers = {1, 3, 5}
        elif selection_type == "low":
            winning_numbers = {1, 2, 3}
        elif selection_type == "high":
            winning_numbers = {4, 5, 6}
        else:
            winning_numbers = set(selected)

        mult = predict_get_multiplier(selected, selection_type)
        won = dice_value in winning_numbers
        win_amount = 0

        if won:
            win_amount = int(round(bet * mult))
            adjust_user_balance(user_id, win_amount, game=True)

        new_balance = get_user_balance(user_id)
        new_balance_usd = new_balance * lc.STARS_TO_USD
        win_usd = win_amount * lc.STARS_TO_USD

        # Update stats
        update_game_stats(user_id, 'predict', bet, win_amount if won else 0, won)

        # Build result message
        if won:
            result_text = (
                f"🏆 {user_link} wins!\n\n"
                f"🎲 Result: <b>{dice_value}</b>\n"
                f"💸 Win: <b>${win_usd:.2f}</b> ({win_amount} ⭐)\n"
                f"🧿 Current balance: <b>${new_balance_usd:.2f}</b> ({new_balance:,} ⭐)"
            )
        else:
            result_text = (
                f"❌ {user_link} lost!\n\n"
                f"🎲 Result: <b>{dice_value}</b>\n"
                f"💸 Win: <b>$0.00</b>\n"
                f"🧿 Current balance: <b>${new_balance_usd:.2f}</b> ({new_balance:,} ⭐)"
            )

        # Send result as a separate message (not edit) so it appears after the dice
        await context.bot.send_message(
            chat_id=chat_id,
            text=result_text,
            parse_mode=ParseMode.HTML
        )

        # Clean up session
        del predict_sessions[user_id]
        return
