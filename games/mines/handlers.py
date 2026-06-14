# -*- coding: utf-8 -*-
"""Mines callback handlers (grid/mines/tile-click/cashout/newgame).

Lifted verbatim from button_callback. Shared, non-rebound helpers and the
mines_games store are imported from librate_casino; the MinesGame engine lives
in games.mines.engine. The dispatcher delegates here for any "mines_"/"mine_click_"
callback (same control-flow position as before).
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from librate_casino import (
    t, get_user_balance, mines_games, update_game_stats, save_data,
    create_mines_grid_keyboard, format_mines_game_message,
    is_admin, adjust_user_balance, user_balances,
)


async def handle_mines_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    if data.startswith("mines_grid_"):
        grid_size = int(data.replace("mines_grid_", ""))

        # Define available mines based on grid size
        if grid_size == 3:
            mines_options = [1, 2, 3, 4]
        elif grid_size == 4:
            mines_options = [1, 3, 5, 7]
        else:  # 5x5
            mines_options = [1, 3, 5, 7, 10, 12]

        keyboard = []
        row = []
        for mines in mines_options:
            total_tiles = grid_size * grid_size
            safe_tiles = total_tiles - mines
            max_multiplier = round((total_tiles / safe_tiles) ** safe_tiles, 2)
            row.append(InlineKeyboardButton(f"{mines} 💣", callback_data=f"mines_mines_{grid_size}_{mines}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"💎 <b>MINES</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Grid Size:</b> <b>{grid_size}×{grid_size}</b>\n"
            f"🎯 <b>Total Tiles:</b> <b>{grid_size * grid_size}</b>\n\n"
            f"💣 <b>Select Number of Mines:</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        return

    if data.startswith("mines_mines_"):
        parts = data.split("_")
        grid_size = int(parts[2])
        num_mines = int(parts[3])

        # Store in context and ask for bet amount
        context.user_data['mines_grid_size'] = grid_size
        context.user_data['mines_num_mines'] = num_mines
        context.user_data['waiting_for_mines_bet'] = True

        total_tiles = grid_size * grid_size
        safe_tiles = total_tiles - num_mines
        max_multiplier = round((total_tiles / safe_tiles) ** safe_tiles, 2)
        balance = get_user_balance(user_id)

        await query.edit_message_text(
            f"💎 <b>MINES</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Game Configuration</b>\n"
            f"Grid: <b>{grid_size}×{grid_size}</b>\n"
            f"Mines: <b>{num_mines}</b> 💣\n"
            f"Safe Tiles: <b>{safe_tiles}</b>\n"
            f"Max Multiplier: <b>{max_multiplier}x</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Your Balance:</b> <b>{balance:,} ⭐</b>\n\n"
            f"💫 <b>Enter your bet amount:</b>\n\n"
            f"Example: <code>100</code> or <code>500</code>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━",
            parse_mode=ParseMode.HTML
        )
        return

    if data.startswith("mine_click_"):
        # Format: mine_click_row_col_game_id
        parts = data.split("_")
        if len(parts) >= 5:
            row = int(parts[2])
            col = int(parts[3])
            game_id = "_".join(parts[4:])

            # Find game by game_id
            game = None
            for uid, g in mines_games.items():
                if g.game_id == game_id:
                    if uid == user_id:  # Verify ownership
                        game = g
                        break

            if not game:
                await query.answer(t("err_game_expired", user_id=user_id), show_alert=True)
                return

            if game.game_state != "playing":
                await query.answer(t("err_game_ended", user_id=user_id), show_alert=True)
                return

            # Check cooldown (1 second)
            time_since_last = (datetime.now() - game.last_click_time).total_seconds()
            if time_since_last < 1:
                await query.answer(t("alert_please_wait", user_id=user_id), show_alert=True)
                return

            # Click tile
            result = game.click_tile(row, col)

            if result is None:
                await query.answer(t("err_tile_opened", user_id=user_id), show_alert=True)
                return

            if result is False:
                # Hit a mine - game over
                game.game_state = "lost"

                # Update game stats (loss) - bet already deducted at start
                update_game_stats(user_id, "mines", game.bet_amount, 0, False)
                save_data()

                # Format professional loss message
                multiplier = game.calculate_multiplier()
                loss_message = "💥 <b>GAME OVER</b> 💥\n\n"
                loss_message += "━━━━━━━━━━━━━━━━━━━━\n"
                loss_message += f"📊 <b>Game Summary</b>\n"
                loss_message += f"Grid: <b>{game.grid_size}×{game.grid_size}</b> | Mines: <b>{game.num_mines}</b> 💣\n"
                loss_message += f"💎 Diamonds Found: <b>{game.diamonds_found}</b>\n"
                loss_message += f"📈 Final Multiplier: <b>{multiplier}x</b>\n\n"
                loss_message += "━━━━━━━━━━━━━━━━━━━━\n"
                loss_message += f"💰 <b>Bet Amount:</b> <b>{game.bet_amount:,} ⭐</b>\n"
                loss_message += f"❌ <b>Result:</b> <b>-{game.bet_amount:,} ⭐</b>\n\n"
                loss_message += "💣 <b>You hit a bomb!</b>\n"
                loss_message += "━━━━━━━━━━━━━━━━━━━━\n\n"

                # Create grid keyboard with all mines revealed
                keyboard = create_mines_grid_keyboard(game)
                keyboard.inline_keyboard.append([InlineKeyboardButton(t("mines_newgame", user_id=user_id), callback_data="mines_newgame")])

                await query.edit_message_text(loss_message, reply_markup=keyboard, parse_mode=ParseMode.HTML)
                del mines_games[user_id]
                return

            # Found diamond - update display
            message = format_mines_game_message(game)
            keyboard = create_mines_grid_keyboard(game)
            await query.edit_message_text(message, reply_markup=keyboard, parse_mode=ParseMode.HTML)
            multiplier = game.calculate_multiplier()
            await query.answer(f"💎 Diamond found! {multiplier}x", show_alert=False)
        return

    if data.startswith("mines_cashout_"):
        game_id = data.replace("mines_cashout_", "")

        # Find game
        game = None
        for uid, g in mines_games.items():
            if g.game_id == game_id:
                if uid == user_id:
                    game = g
                    break

        if not game:
            await query.answer(t("err_game_not_found", user_id=user_id), show_alert=True)
            return

        if game.game_state != "playing":
            await query.answer(t("err_game_ended", user_id=user_id), show_alert=True)
            return

        # Cash out
        win_amount = game.cash_out()
        profit = win_amount - game.bet_amount
        multiplier = game.calculate_multiplier()

        # Add winnings (bet was already deducted at start)
        if not is_admin(user_id):
            paid = adjust_user_balance(user_id, win_amount, game=True)
            if paid is False:
                await query.answer("🔧 Casino Maintenance — unable to process win. Try again shortly.", show_alert=True)
                return
            user_balances[user_id] = get_user_balance(user_id)  # Sync memory cache

        # Update game stats
        update_game_stats(user_id, "mines", game.bet_amount, win_amount, True)

        # Format professional win message
        win_message = "✅ <b>CASHED OUT!</b> ✅\n\n"
        win_message += "━━━━━━━━━━━━━━━━━━━━\n"
        win_message += f"📊 <b>Game Summary</b>\n"
        win_message += f"Grid: <b>{game.grid_size}×{game.grid_size}</b> | Mines: <b>{game.num_mines}</b> 💣\n"
        win_message += f"💎 Diamonds Found: <b>{game.diamonds_found}</b>\n"
        win_message += f"📈 Final Multiplier: <b>{multiplier}x</b>\n\n"
        win_message += "━━━━━━━━━━━━━━━━━━━━\n"
        win_message += f"💰 <b>Bet Amount:</b> <b>{game.bet_amount:,} ⭐</b>\n"
        win_message += f"💵 <b>Win Amount:</b> <b>{win_amount:,} ⭐</b>\n"
        win_message += f"📊 <b>Profit:</b> <b>+{profit:,} ⭐</b>\n\n"
        win_message += "🎉 <b>Congratulations!</b>\n"
        win_message += "━━━━━━━━━━━━━━━━━━━━\n\n"

        # Create final grid display - show opened diamonds and unopened tiles
        grid_text = "<b>Final Grid:</b>\n"
        for r in range(game.grid_size):
            for c in range(game.grid_size):
                if (r, c) in game.opened_tiles:
                    grid_text += "💎 "
                else:
                    grid_text += "⬜ "
            grid_text += "\n"

        win_message += grid_text

        keyboard = [[InlineKeyboardButton(t("mines_newgame", user_id=user_id), callback_data="mines_newgame")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(win_message, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        del mines_games[user_id]
        return

    if data == "mines_newgame":
        # Reset to grid selection
        keyboard = [
            [
                InlineKeyboardButton("3×3", callback_data="mines_grid_3"),
                InlineKeyboardButton("4×4", callback_data="mines_grid_4"),
                InlineKeyboardButton("5×5", callback_data="mines_grid_5"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        balance = get_user_balance(user_id)

        await query.edit_message_text(
            "💎 <b>MINES</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Your Balance:</b> <b>{balance:,} ⭐</b>\n\n"
            "🎯 <b>Select Grid Size:</b>\n\n"
            "• <b>3×3</b> - 9 tiles (1-4 mines)\n"
            "• <b>4×4</b> - 16 tiles (1-7 mines)\n"
            "• <b>5×5</b> - 25 tiles (1-12 mines)\n\n"
            "━━━━━━━━━━━━━━━━━━━━",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        return
