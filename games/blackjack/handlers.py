# -*- coding: utf-8 -*-
"""Blackjack handlers: /blackjack + all bj_* callbacks, table sending, actions, payout.

Lifted verbatim from librate_casino. Engine/renderer live in
optimus/games/blackjack_engine.py; shared non-rebound helpers + blackjack_sessions
imported from librate_casino. Re-imported into librate_casino so button_callback
(bj_ delegation) and main resolve them.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from optimus.games.blackjack_engine import (
    bj_create_deck, bj_card_points, bj_calculate_score, bj_calculate_visible_score,
    bj_hand_str, bj_generate_table_image, bj_resolve,
)
from librate_casino import (
    t, handle_errors, is_banned, get_user_balance, adjust_user_balance,
    update_game_stats, user_balances, register_menu_owner, blackjack_sessions,
)


def bj_action_buttons(session, user_id=None):
    """Build inline keyboard for current game state."""
    player_cards = session["player_cards"]
    has_two = len(player_cards) == 2
    can_split = has_two and player_cards[0]["value"] == player_cards[1]["value"]
    # For split hands, no further split/double
    is_split_hand = session.get("split_hand_index") is not None

    row1 = [
        InlineKeyboardButton(t("bj_hit", user_id=user_id), callback_data="bj_hit"),
        InlineKeyboardButton(t("bj_stand", user_id=user_id), callback_data="bj_stand"),
    ]
    row2 = []
    if has_two and not is_split_hand:
        row2.append(InlineKeyboardButton(t("bj_double", user_id=user_id), callback_data="bj_double"))
    if can_split and not is_split_hand:
        row2.append(InlineKeyboardButton(t("bj_split", user_id=user_id), callback_data="bj_split"))
    row3 = [InlineKeyboardButton(t("bj_forfeit_btn", user_id=user_id), callback_data="bj_forfeit")]

    keyboard = [row1]
    if row2:
        keyboard.append(row2)
    keyboard.append(row3)
    return InlineKeyboardMarkup(keyboard)


async def bj_send_table(context, session, hide_bot_second=True, result_text=None, reply_markup=None, caption=None):
    """Generate table image and send/edit the game message."""
    img = bj_generate_table_image(
        session["player_cards"], session["bot_cards"],
        hide_bot_second=hide_bot_second, result_text=result_text
    )
    chat_id = session["chat_id"]
    msg_id = session.get("message_id")

    if msg_id:
        # Try to edit existing photo message
        try:
            from telegram import InputMediaPhoto
            media = InputMediaPhoto(media=img, caption=caption, parse_mode=ParseMode.HTML if caption else None)
            msg = await context.bot.edit_message_media(
                chat_id=chat_id, message_id=msg_id,
                media=media, reply_markup=reply_markup
            )
            session["message_id"] = msg.message_id
            return msg
        except Exception as e:
            logger.debug(f"BJ edit_message_media failed, sending new: {e}")

    # Send new photo
    msg = await context.bot.send_photo(
        chat_id=chat_id, photo=img, caption=caption,
        parse_mode=ParseMode.HTML if caption else None,
        reply_markup=reply_markup
    )
    session["message_id"] = msg.message_id
    return msg


async def bj_finish_game(context, session, user_id, result_type, payout):
    """Handle end-of-game: credit winnings, send final image, show result."""
    bet = session["bet"]
    player_score = bj_calculate_score(session["player_cards"])
    bot_score = bj_calculate_score(session["bot_cards"])
    player_hand = bj_hand_str(session["player_cards"])
    bot_hand = bj_hand_str(session["bot_cards"])

    if payout > 0:
        adjust_user_balance(user_id, payout, game=True)
        user_balances[user_id] = get_user_balance(user_id)

    profit = payout - bet
    is_win = result_type in ("win", "blackjack")
    update_game_stats(user_id, 'blackjack', bet, payout if is_win else 0, is_win)

    # Result banner text for image
    banner_map = {
        "blackjack": "BLACKJACK!",
        "win": "YOU WIN!",
        "loss": "YOU LOSE",
        "bust": "BUSTED!",
        "push": "PUSH - TIE",
        "forfeit": "FORFEITED",
    }
    banner = banner_map.get(result_type, "GAME OVER")

    # Send final table image
    await bj_send_table(context, session, hide_bot_second=False, result_text=banner)

    # Build result caption message
    if result_type == "blackjack":
        text = (
            f"ð <b>BLACKJACK!</b> 🎉\n\n"
            f"Your hand: {player_hand} = {player_score}\n"
            f"Bot hand: {bot_hand} = {bot_score}\n\n"
            f"💰 Blackjack pays 1.5x! You earned: <b>{payout} ⭐</b>"
        )
    elif result_type == "win":
        text = (
            f"ð <b>Blackjack Result</b>\n"
            f"✅ <b>You Win!</b>\n\n"
            f"Your hand: {player_hand} = {player_score}\n"
            f"Bot hand: {bot_hand} = {bot_score}"
            + (f" (Bust!)" if bot_score > 21 else "") +
            f"\n\n💰 You earned: <b>{payout} ⭐</b>"
        )
    elif result_type == "bust":
        text = (
            f"ð <b>Blackjack Result</b>\n"
            f"❌ <b>You Busted!</b>\n\n"
            f"Your hand: {player_hand} = {player_score}\n"
            f"Bot hand: {bot_hand} = {bot_score}\n\n"
            f"💸 You lost: <b>{bet} ⭐</b>"
        )
    elif result_type == "loss":
        text = (
            f"ð <b>Blackjack Result</b>\n"
            f"❌ <b>You Lose!</b>\n\n"
            f"Your hand: {player_hand} = {player_score}\n"
            f"Bot hand: {bot_hand} = {bot_score}\n\n"
            f"💸 You lost: <b>{bet} ⭐</b>"
        )
    elif result_type == "push":
        text = (
            f"ð <b>Push! It's a tie.</b>\n\n"
            f"Your hand: {player_hand} = {player_score}\n"
            f"Bot hand: {bot_hand} = {bot_score}\n\n"
            f"↩️ Bet returned: <b>{bet} ⭐</b>"
        )
    elif result_type == "forfeit":
        returned = payout
        text = (
            f"ð <b>Blackjack Result</b>\n"
            f"🔴 <b>Forfeited</b>\n\n"
            f"Your hand: {player_hand} = {player_score}\n\n"
            f"↩️ Half bet returned: <b>{returned} ⭐</b>"
        )
    else:
        text = f"ð Game Over\n💰 Payout: {payout} â­"

    # Play again button
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(t("btn_play_again", user_id=user_id), callback_data="bj_play_again")]
    ])

    await context.bot.send_message(
        chat_id=session["chat_id"],
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )

    # Clean up session
    if user_id in blackjack_sessions:
        del blackjack_sessions[user_id]


@handle_errors
async def blackjack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bj command - show bet selection."""
    if not update.message:
        return
    user_id = update.effective_user.id
    username = update.effective_user.username or ''

    if is_banned(user_id):
        return

    get_or_create_profile(user_id, username)

    if user_id in blackjack_sessions:
        await update.message.reply_html(t("bj_active_game2", user_id=user_id))
        return

    balance = get_user_balance(user_id)
    if balance <= 0:
        await update.message.reply_html(t("bj_insufficient", user_id=user_id))
        return

    # Check for inline amount: /bj 100
    if context.args:
        try:
            bet = int(context.args[0])
            if bet < 10:
                await update.message.reply_html(t("bj_min_bet", user_id=user_id))
                return
            if balance < bet:
                await update.message.reply_html(t("bj_insufficient2", user_id=user_id, balance=balance))
                return
            await bj_start_game(context, update, user_id, bet)
            return
        except ValueError:
            pass

    # Build bet menu
    keyboard = [
        [
            InlineKeyboardButton("50 ⭐", callback_data="bj_bet_50"),
            InlineKeyboardButton("100 ⭐", callback_data="bj_bet_100"),
            InlineKeyboardButton("250 ⭐", callback_data="bj_bet_250"),
        ],
        [
            InlineKeyboardButton("500 ⭐", callback_data="bj_bet_500"),
            InlineKeyboardButton("1000 ⭐", callback_data="bj_bet_1000"),
            InlineKeyboardButton(t("btn_custom_bet", user_id=user_id), callback_data="bj_bet_custom"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"ð <b>Blackjack</b>\n\n"
        f"Select your bet amount:\n\n"
        f"💳 Balance: <b>{balance} ⭐</b>"
    )

    sent = await update.message.reply_html(text, reply_markup=reply_markup)
    register_menu_owner(sent, user_id)


async def bj_start_game(context, update_or_none, user_id, bet):
    """Start a new blackjack game after bet is confirmed."""
    balance = get_user_balance(user_id)
    if balance < bet:
        return

    if user_id in blackjack_sessions:
        return

    # Deduct bet
    adjust_user_balance(user_id, -bet, game=True)
    user_balances[user_id] = get_user_balance(user_id)

    # Create deck and deal
    deck = bj_create_deck()
    player_cards = [deck.pop(), deck.pop()]
    bot_cards = [deck.pop(), deck.pop()]

    session = {
        "deck": deck,
        "player_cards": player_cards,
        "bot_cards": bot_cards,
        "bet": bet,
        "state": "playing",
        "message_id": None,
        "chat_id": None,
        "user_id": user_id,
    }

    # Determine chat_id from update context
    if update_or_none and hasattr(update_or_none, 'effective_chat'):
        session["chat_id"] = update_or_none.effective_chat.id
    elif update_or_none and hasattr(update_or_none, 'message') and update_or_none.message:
        session["chat_id"] = update_or_none.message.chat_id
    elif update_or_none and hasattr(update_or_none, 'callback_query') and update_or_none.callback_query:
        session["chat_id"] = update_or_none.callback_query.message.chat_id

    blackjack_sessions[user_id] = session

    # Check for natural blackjack
    player_score = bj_calculate_score(player_cards)
    is_natural = player_score == 21 and len(player_cards) == 2

    if is_natural:
        bot_score = bj_calculate_score(bot_cards)
        result_type, payout = bj_resolve(player_score, bot_score, bet, is_natural_bj=True)

        # Send final image immediately
        msg = await bj_send_table(context, session, hide_bot_second=False)
        session["message_id"] = msg.message_id
        await bj_finish_game(context, session, user_id, result_type, payout)
        return

    # Normal game: send table image with action buttons
    reply_markup = bj_action_buttons(session)
    caption = f"ð Blackjack | Bet: {bet} â­ | Your turn!"
    msg = await bj_send_table(context, session, hide_bot_second=True, reply_markup=reply_markup, caption=caption)
    session["message_id"] = msg.message_id
    register_menu_owner(msg, user_id)


async def bj_bot_turn(context, session, user_id):
    """Execute bot's turn: reveal card, draw until 17+, resolve."""
    session["state"] = "bot_turn"

    # Reveal hidden card
    await bj_send_table(context, session, hide_bot_second=False,
                        caption="🤖 Bot's turn...")

    await asyncio.sleep(1)

    # Bot draws until 17+
    while bj_calculate_score(session["bot_cards"]) < 17:
        session["bot_cards"].append(session["deck"].pop())
        bot_score = bj_calculate_score(session["bot_cards"])
        await bj_send_table(context, session, hide_bot_second=False,
                            caption=f"🤖 Bot draws... ({bot_score})")
        await asyncio.sleep(1)

    # Resolve
    player_score = bj_calculate_score(session["player_cards"])
    bot_score = bj_calculate_score(session["bot_cards"])
    result_type, payout = bj_resolve(player_score, bot_score, session["bet"])
    await bj_finish_game(context, session, user_id, result_type, payout)


async def bj_advance_split(context, session, user_id):
    """After finishing a split hand, advance to next hand or resolve both."""
    idx = session["split_hand_index"]
    # Save current hand score for result
    score = bj_calculate_score(session["player_cards"])
    session["split_results"][idx] = score

    if idx == 0:
        # Switch to hand 2
        session["split_hand_index"] = 1
        session["player_cards"] = session["split_hands"][1]
        session["bet"] = session["split_bets"][1]
        session["state"] = "playing"
        session["message_id"] = None  # Force new message for hand 2

        reply_markup = bj_action_buttons(session)
        hand2_score = bj_calculate_score(session["player_cards"])
        caption = f"ð Split Hand 2/2 | Bet: {session['bet']} â­ | Score: {hand2_score}"
        msg = await bj_send_table(context, session, hide_bot_second=True, reply_markup=reply_markup, caption=caption)
        session["message_id"] = msg.message_id
    else:
        # Both hands done — bot plays, then resolve each hand
        session["state"] = "bot_turn"

        # Bot draws (using combined view of last hand for the image)
        await bj_send_table(context, session, hide_bot_second=False,
                            caption="🤖 Bot's turn...")
        await asyncio.sleep(1)

        while bj_calculate_score(session["bot_cards"]) < 17:
            session["bot_cards"].append(session["deck"].pop())
            bot_score = bj_calculate_score(session["bot_cards"])
            await bj_send_table(context, session, hide_bot_second=False,
                                caption=f"🤖 Bot draws... ({bot_score})")
            await asyncio.sleep(1)

        bot_score = bj_calculate_score(session["bot_cards"])
        total_payout = 0
        results_text = []

        for hi in range(2):
            hand = session["split_hands"][hi]
            hand_score = bj_calculate_score(hand)
            hand_bet = session["split_bets"][hi]
            hand_str = bj_hand_str(hand)

            if hand_score > 21:
                results_text.append(f"Hand {hi+1}: {hand_str} = {hand_score} (Bust!) — Lost {hand_bet} ⭐")
            else:
                res_type, payout = bj_resolve(hand_score, bot_score, hand_bet)
                total_payout += payout
                if res_type == "win":
                    results_text.append(f"Hand {hi+1}: {hand_str} = {hand_score} — Won {payout} ⭐")
                elif res_type == "push":
                    results_text.append(f"Hand {hi+1}: {hand_str} = {hand_score} — Push (returned {payout} ⭐)")
                else:
                    results_text.append(f"Hand {hi+1}: {hand_str} = {hand_score} — Lost {hand_bet} ⭐")

        if total_payout > 0:
            adjust_user_balance(user_id, total_payout, game=True)
            user_balances[user_id] = get_user_balance(user_id)

        total_bet = session["split_bets"][0] + session["split_bets"][1]
        is_win = total_payout > total_bet
        update_game_stats(user_id, 'blackjack', total_bet, total_payout if is_win else 0, is_win)

        bot_hand = bj_hand_str(session["bot_cards"])
        result_lines = "\n".join(results_text)
        text = (
            f"ð <b>Split Results</b>\n\n"
            f"{result_lines}\n\n"
            f"Bot: {bot_hand} = {bot_score}\n\n"
            f"💰 Total payout: <b>{total_payout} ⭐</b>"
        )

        # Final image with last hand shown
        await bj_send_table(context, session, hide_bot_second=False, result_text="SPLIT RESULT")

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(t("bj_play_again", user_id=user_id), callback_data="bj_play_again")]
        ])
        await context.bot.send_message(
            chat_id=session["chat_id"], text=text,
            parse_mode=ParseMode.HTML, reply_markup=keyboard
        )

        if user_id in blackjack_sessions:
            del blackjack_sessions[user_id]


async def bj_hand_complete(context, session, user_id, busted=False):
    """Called when current hand is done (stand or bust). Handles split routing."""
    if session.get("split_hand_index") is not None:
        if busted:
            session["split_results"][session["split_hand_index"]] = bj_calculate_score(session["player_cards"])
        await bj_advance_split(context, session, user_id)
    else:
        if busted:
            session["state"] = "finished"
            await bj_finish_game(context, session, user_id, "bust", 0)
        else:
            await bj_bot_turn(context, session, user_id)


async def bj_handle_hit(query, context, user_id):
    """Handle hit action."""
    session = blackjack_sessions.get(user_id)
    if not session or session["state"] != "playing":
        await query.answer(t("bj_no_active", user_id=user_id), show_alert=True)
        return

    await query.answer()
    session["player_cards"].append(session["deck"].pop())
    player_score = bj_calculate_score(session["player_cards"])

    if player_score > 21:
        await bj_hand_complete(context, session, user_id, busted=True)
        return

    if player_score == 21:
        await bj_hand_complete(context, session, user_id, busted=False)
        return

    # Continue playing
    reply_markup = bj_action_buttons(session, user_id)
    hand_label = ""
    if session.get("split_hand_index") is not None:
        hand_label = f"Hand {session['split_hand_index']+1}/2 | "
    caption = f"ð {hand_label}Bet: {session['bet']} â­ | Score: {player_score}"
    await bj_send_table(context, session, hide_bot_second=True, reply_markup=reply_markup, caption=caption)


async def bj_handle_stand(query, context, user_id):
    """Handle stand action."""
    session = blackjack_sessions.get(user_id)
    if not session or session["state"] != "playing":
        await query.answer(t("bj_no_active", user_id=user_id), show_alert=True)
        return

    await query.answer()
    await bj_hand_complete(context, session, user_id, busted=False)


async def bj_handle_double(query, context, user_id):
    """Handle double down: double bet, deal one card, auto-stand."""
    session = blackjack_sessions.get(user_id)
    if not session or session["state"] != "playing":
        await query.answer(t("bj_no_active", user_id=user_id), show_alert=True)
        return

    if len(session["player_cards"]) != 2:
        await query.answer(t("bj_double_first_only", user_id=user_id), show_alert=True)
        return

    bet = session["bet"]
    balance = get_user_balance(user_id)
    if balance < bet:
        await query.answer(t("bj_insufficient_double", user_id=user_id), show_alert=True)
        return

    await query.answer()

    # Deduct additional bet
    adjust_user_balance(user_id, -bet, game=True)
    user_balances[user_id] = get_user_balance(user_id)
    session["bet"] = bet * 2
    # Update split bet if applicable
    if session.get("split_hand_index") is not None:
        session["split_bets"][session["split_hand_index"]] = bet * 2

    # Deal exactly one card
    session["player_cards"].append(session["deck"].pop())
    player_score = bj_calculate_score(session["player_cards"])

    if player_score > 21:
        await bj_hand_complete(context, session, user_id, busted=True)
        return

    # Auto-stand after double
    await bj_hand_complete(context, session, user_id, busted=False)


async def bj_handle_split(query, context, user_id):
    """Handle split: split pair into two hands played sequentially."""
    session = blackjack_sessions.get(user_id)
    if not session or session["state"] != "playing":
        await query.answer(t("bj_no_active", user_id=user_id), show_alert=True)
        return

    cards = session["player_cards"]
    if len(cards) != 2 or cards[0]["value"] != cards[1]["value"]:
        await query.answer(t("bj_split_pair_only", user_id=user_id), show_alert=True)
        return

    bet = session["bet"]
    balance = get_user_balance(user_id)
    if balance < bet:
        await query.answer(t("bj_insufficient_split", user_id=user_id), show_alert=True)
        return

    await query.answer()

    # Deduct additional bet for second hand
    adjust_user_balance(user_id, -bet, game=True)
    user_balances[user_id] = get_user_balance(user_id)

    # Create two hands
    card1, card2 = cards[0], cards[1]
    hand1 = [card1, session["deck"].pop()]
    hand2 = [card2, session["deck"].pop()]

    session["split_hands"] = [hand1, hand2]
    session["split_bets"] = [bet, bet]
    session["split_results"] = [None, None]
    session["split_hand_index"] = 0
    session["player_cards"] = hand1  # Play first hand
    session["original_bet"] = bet

    # Show first hand
    reply_markup = bj_action_buttons(session, user_id)
    score = bj_calculate_score(hand1)
    caption = f"ð Split Hand 1/2 | Bet: {bet} â­ | Score: {score}"
    await bj_send_table(context, session, hide_bot_second=True, reply_markup=reply_markup, caption=caption)


async def bj_handle_forfeit(query, context, user_id):
    """Handle forfeit: return half the bet."""
    session = blackjack_sessions.get(user_id)
    if not session or session["state"] != "playing":
        await query.answer(t("bj_no_active", user_id=user_id), show_alert=True)
        return

    await query.answer()
    session["state"] = "finished"
    half_bet = session["bet"] // 2
    await bj_finish_game(context, session, user_id, "forfeit", half_bet)


async def handle_blackjack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route all bj_ callbacks."""
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    # Play again
    if data == "bj_play_again":
        await query.answer()
        # Simulate /bj command
        balance = get_user_balance(user_id)
        if balance <= 0:
            await query.answer(t("insufficient_balance", user_id=user_id), show_alert=True)
            return
        if user_id in blackjack_sessions:
            await query.answer(t("bj_game_active", user_id=user_id), show_alert=True)
            return
        keyboard = [
            [
                InlineKeyboardButton("50 ⭐", callback_data="bj_bet_50"),
                InlineKeyboardButton("100 ⭐", callback_data="bj_bet_100"),
                InlineKeyboardButton("250 ⭐", callback_data="bj_bet_250"),
            ],
            [
                InlineKeyboardButton("500 ⭐", callback_data="bj_bet_500"),
                InlineKeyboardButton("1000 ⭐", callback_data="bj_bet_1000"),
                InlineKeyboardButton(t("bj_custom_btn", user_id=user_id), callback_data="bj_bet_custom"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = f"ð <b>{t('bj_title', user_id=user_id).replace('ð ', '')}</b>\n\n{t('bj_select_bet', user_id=user_id)}\n\n{t('bj_balance_label', user_id=user_id)}: <b>{balance} â­</b>"
        sent = await context.bot.send_message(
            chat_id=query.message.chat_id, text=text,
            parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )
        register_menu_owner(sent, user_id)
        return

    # Bet selection
    if data.startswith("bj_bet_"):
        bet_str = data.replace("bj_bet_", "")

        if bet_str == "custom":
            await query.answer()
            context.user_data["bj_custom_bet_pending"] = {
                "chat_id": query.message.chat_id,
                "message_id": query.message.message_id,
            }
            await query.edit_message_text(
                f"ð <b>Blackjack - Custom Bet</b>\n\n"
                f"💰 Type your bet amount in stars (e.g. <code>150</code>)\n"
                f"Minimum: 10 ⭐",
                parse_mode=ParseMode.HTML,
            )
            return

        try:
            bet = int(bet_str)
        except ValueError:
            await query.answer()
            return

        if bet < 10:
            await query.answer(t("bj_min_bet_alert", user_id=user_id), show_alert=True)
            return

        balance = get_user_balance(user_id)
        if balance < bet:
            await query.answer(t("err_insuf_bal_alert", user_id=user_id), show_alert=True)
            return

        if user_id in blackjack_sessions:
            await query.answer(t("bj_game_active", user_id=user_id), show_alert=True)
            return

        await query.answer()
        # Delete the bet menu message
        try:
            await query.message.delete()
        except Exception:
            pass
        await bj_start_game(context, update, user_id, bet)
        return

    # Session-based actions: verify ownership
    session = blackjack_sessions.get(user_id)
    action_callbacks = ("bj_hit", "bj_stand", "bj_double", "bj_split", "bj_forfeit")
    if data in action_callbacks and not session:
        for uid, s in blackjack_sessions.items():
            if (s.get("chat_id") == query.message.chat_id
                    and s.get("message_id") == query.message.message_id
                    and uid != user_id):
                await query.answer(t("err_not_your_game", user_id=user_id), show_alert=True)
                return
        await query.answer(t("bj_no_active", user_id=user_id), show_alert=True)
        return

    if data in action_callbacks and session:
        if session.get("state") != "playing":
            await query.answer(t("alert_please_wait", user_id=user_id), show_alert=True)
            return

    # Route game actions
    if data == "bj_hit":
        await bj_handle_hit(query, context, user_id)
    elif data == "bj_stand":
        await bj_handle_stand(query, context, user_id)
    elif data == "bj_double":
        await bj_handle_double(query, context, user_id)
    elif data == "bj_split":
        await bj_handle_split(query, context, user_id)
    elif data == "bj_forfeit":
        await bj_handle_forfeit(query, context, user_id)
