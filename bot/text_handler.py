# -*- coding: utf-8 -*-
"""Free-text message router (handle_text_message).

Routes plain text to the active flow: deposit/withdraw amounts, gift chat-id,
steal/emoji/template setup, blackjack/mines bet entry, broadcast capture, etc.
Lifted VERBATIM except the global-state bridge: MIN_WITHDRAWAL and
casino_bankroll_usd (rebound here) plus user_balances/user_crypto_balances via
``lc.*``. ``bj_start_game`` is undefined in the original module too (blackjack
runs its own bet flow); left bare so that legacy branch raises the same
NameError. Re-imported last so all delegated flow handlers are on lc.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes

import games.claw as claw

import librate_casino as lc
from librate_casino import (
    MinesGame, PROVIDER_TOKEN, STARS_TO_TON, adjust_user_balance, blackjack_sessions, broadcast_waiting,
    create_mines_grid_keyboard, db, detect_lang, emoji_replace_flow, format_mines_game_message, get_command_message_preview,
    get_user_balance, handle_emoji_flow_input, handle_errors, handle_steal_flow, is_admin, is_banned,
    is_valid_crypto_address, is_valid_ton_address, logger, mines_games, perform_broadcast, process_gift_after_payment,
    process_gift_chat_id, register_menu_owner, save_template, t, template_setup_mode, translate_text,
    user_languages,
)


@handle_errors
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    
    # Auto-detect and set language on any message (if not already set)
    if user_id not in user_languages:
        user_lang_code = getattr(user, 'language_code', None) or ""
        detected = detect_lang(user_lang_code)
        user_languages[user_id] = detected
        db.set_user_language(user_id, detected)
    
    # Check if user is banned (allow admins and special flows)
    if is_banned(user_id) and not is_admin(user_id):
        # Allow admin flows even if admin is somehow banned (shouldn't happen)
        if not context.user_data.get('steal_state') and not context.user_data.get('waiting_for_bankroll') and not context.user_data.get('waiting_for_min_withdrawal'):
            return  # Silently ignore banned users
    
    message = update.message or update.edited_message
    if not message:
        return
    text = (message.text or "").strip()
    
    # Check claw sticker admin input
    import games.claw as claw
    handled = await claw.handle_claw_sticker_input(update, context)
    if handled:
        return
    
    # Handle emoji replacement flow (admin only) — must be checked before other handlers
    if user_id in emoji_replace_flow:
        consumed = await handle_emoji_flow_input(update, context)
        if consumed:
            return

    # Handle template setup mode (admin only)
    if user_id in template_setup_mode and template_setup_mode[user_id].get("active"):
        setup_state = template_setup_mode[user_id]
        
        # Check for /done or /cancel
        text_lower = text.lower()
        if text_lower == "/done":
            template_setup_mode[user_id] = {"active": False}
            await update.message.reply_html(t("emoji_template_exit", user_id=user_id))
            return
        if text_lower == "/cancel":
            template_setup_mode[user_id] = {"active": False}
            await update.message.reply_html(t("emoji_template_cancelled", user_id=user_id))
            return
        
        # If waiting for command name
        if setup_state.get("waiting_for_command"):
            command_name = text.strip().lower().replace("/", "")
            if not command_name:
                await update.message.reply_html(t("emoji_invalid_command", user_id=user_id))
                return
            
            # Get current message for this command (for preview)
            current_message = get_command_message_preview(command_name, user_id)
            
            # Send the current message to admin and ask for new template
            await update.message.reply_html(
                f"📋 <b>Current message for /{command_name}:</b>\n\n"
                f"{current_message}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"✅ Now send the <b>message with emojis & variables</b> (e.g., \"Welcome {{username}}! 🎯✅\").\n\n"
                f"You can include:\n"
                f"• Premium/custom emojis (preserved)\n"
                f"• Variables: <code>{{username}}</code>, <code>{{balance}}</code>, <code>{{amount}}</code>\n"
                f"• Inline buttons and links (optional)\n"
                f"• HTML formatting"
            )
            
            template_setup_mode[user_id] = {
                "active": True,
                "current_command": command_name,
                "waiting_for_command": False,
                "waiting_for_message": True
            }
            return
        # If waiting for message template (single step)
        if setup_state.get("waiting_for_message"):
            command_name = setup_state.get("current_command")
            if not command_name:
                await update.message.reply_html(t("emoji_no_command_set", user_id=user_id))
                template_setup_mode[user_id] = {"active": False}
                return
            
            # Capture message HTML, entities, and reply_markup (for inline buttons)
            message = update.message
            html_content = message.html_text if hasattr(message, 'html_text') else message.text or ""
            
            if not html_content:
                await update.message.reply_html(t("emoji_invalid_message", user_id=user_id))
                return
            
            # Get entities (for custom emojis and links)
            entities = []
            if message.entities:
                for entity in message.entities:
                    entity_dict = {
                        "type": entity.type.name if hasattr(entity.type, 'name') else str(entity.type),
                        "offset": entity.offset,
                        "length": entity.length
                    }
                    # Preserve custom_emoji_id if present
                    if hasattr(entity, 'custom_emoji_id'):
                        entity_dict["custom_emoji_id"] = entity.custom_emoji_id
                    # Preserve URL for text_link
                    entity_type_str = entity.type.name if hasattr(entity.type, 'name') else str(entity.type)
                    if entity_type_str == 'text_link' and hasattr(entity, 'url'):
                        entity_dict["url"] = entity.url
                    entities.append(entity_dict)
            
            # Get reply_markup (inline keyboard) if present
            reply_markup = None
            if message.reply_markup and hasattr(message.reply_markup, 'inline_keyboard'):
                reply_markup = []
                for row in message.reply_markup.inline_keyboard:
                    button_row = []
                    for button in row:
                        button_dict = {
                            "text": button.text
                        }
                        if hasattr(button, 'callback_data') and button.callback_data:
                            button_dict["callback_data"] = button.callback_data
                        if hasattr(button, 'url') and button.url:
                            button_dict["url"] = button.url
                        if hasattr(button, 'web_app') and button.web_app:
                            # Store web_app as string representation
                            button_dict["web_app"] = str(button.web_app.url) if hasattr(button.web_app, 'url') else str(button.web_app)
                        button_row.append(button_dict)
                    reply_markup.append(button_row)
            
            # Save template (upsert on duplicate)
            save_template(command_name, html_content, entities, reply_markup)
            
            await update.message.reply_html(
                f"✅ Template saved for <code>/{command_name}</code>!\n\n"
                "Send another command name to set another template, or /done to finish."
            )
            
            # Reset to wait for next command
            template_setup_mode[user_id] = {
                "active": True,
                "current_command": None,
                "waiting_for_command": True
            }
            return
    
    # Handle steal command flow
    if context.user_data.get('steal_state'):
        await handle_steal_flow(update, context)
        return
    
    # Handle bankroll input from admin prompt
    if context.user_data.get('waiting_for_bankroll'):
        if not is_admin(user_id):
            context.user_data['waiting_for_bankroll'] = False
            await update.message.reply_html(translate_text("❌ Only admins can set bankroll.", user_id=user_id))
            return
        try:
            amount = float(text)
            lc.casino_bankroll_usd = amount
            db.set_casino_bankroll(amount)
            context.user_data['waiting_for_bankroll'] = False
            await update.message.reply_html(
                translate_text(f"✅ Bankroll updated.\n\n🏦 Casino Bankroll\n💵 USD: ${lc.casino_bankroll_usd:,.2f}", user_id=user_id)
            )
        except ValueError:
            await update.message.reply_html(translate_text("❌ Please enter a valid number (e.g., 2493.23).", user_id=user_id))
        return
    
    # Handle minimum withdrawal input (admin only)
    if context.user_data.get('waiting_for_min_withdrawal'):
        if not is_admin(user_id):
            context.user_data['waiting_for_min_withdrawal'] = False
            await update.message.reply_html(translate_text("❌ Only admins can set minimum withdrawal.", user_id=user_id))
            return
        try:
            amount = int(text)
            if amount < 1:
                await update.message.reply_html(translate_text("❌ Minimum withdrawal must be at least 1 ⭐", user_id=user_id))
                return
            lc.MIN_WITHDRAWAL = amount
            context.user_data['waiting_for_min_withdrawal'] = False
            await update.message.reply_html(
                f"✅ <b>Minimum withdrawal updated!</b>\n\n"
                f"💰 New minimum: <b>{lc.MIN_WITHDRAWAL} ⭐</b>"
            )
            logger.info(f"Admin {user_id} set minimum withdrawal to {lc.MIN_WITHDRAWAL}")
        except ValueError:
            await update.message.reply_html(translate_text("❌ Please enter a valid integer number (e.g., 200)."))
        return

    # Handle gift chat ID input (Step 2)
    if context.user_data.get('gift_state') == 'waiting_for_chat_id':
        await process_gift_chat_id(update, context, text)
        return
    
    # Handle "1" as payment shortcut after /pingme (Step 3 shortcut)
    if context.user_data.get('gift_state') == 'waiting_for_payment' and text.strip() == "1":
        if not is_admin(user_id):
            return
        # Treat "1" as payment confirmation - process gift automatically
        logger.info(f"Admin {user_id}: Received '1' as payment shortcut, processing gift")
        await update.message.reply_html(translate_text("✅ <b>Payment confirmed!</b>\n\n🎂 <b>Processing gift...</b>", user_id=user_id))
        await process_gift_after_payment(update, context)
        return
    
    # Handle broadcast text (admin only, waiting flag set via /broadcast)
    if user_id in broadcast_waiting and update.effective_chat.type == "private":
        if not is_admin(user_id):
            broadcast_waiting.discard(user_id)
            return
        await perform_broadcast(update, context, update.message)
        broadcast_waiting.discard(user_id)
        return
    
    # Handle mines bet amount input
    if context.user_data.get('waiting_for_mines_bet'):
        if update.effective_chat.type != "private":
            return
        
        try:
            bet_amount = int(text)
            balance = get_user_balance(user_id)
            
            if bet_amount < 1:
                await update.message.reply_html(
                    "❌ <b>Invalid Bet Amount</b>\n\n"
                    "Minimum bet is <b>1 ⭐</b>"
                )
                return
            
            if bet_amount > balance:
                await update.message.reply_html(
                    f"❌ <b>Insufficient Balance</b>\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 <b>Your Balance:</b> <b>{balance:,} ⭐</b>\n"
                    f"💵 <b>Requested:</b> <b>{bet_amount:,} ⭐</b>\n"
                    f"📊 <b>Shortage:</b> <b>{bet_amount - balance:,} ⭐</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
                return
            
            grid_size = context.user_data.get('mines_grid_size')
            num_mines = context.user_data.get('mines_num_mines')
            
            if not grid_size or not num_mines:
                await update.message.reply_html(translate_text("❌ Error: Game settings not found. Please start again with /mines", user_id=user_id))
                context.user_data['waiting_for_mines_bet'] = False
                return
            
            # Deduct bet
            if not is_admin(user_id):
                adjust_user_balance(user_id, -bet_amount, game=True)
                lc.user_balances[user_id] = get_user_balance(user_id)  # Sync memory cache

            # Create game
            game = MinesGame(user_id, grid_size, num_mines, bet_amount)
            mines_games[user_id] = game
            
            context.user_data['waiting_for_mines_bet'] = False
            context.user_data['mines_grid_size'] = None
            context.user_data['mines_num_mines'] = None
            
            # Show game
            message = format_mines_game_message(game)
            keyboard = create_mines_grid_keyboard(game)
            await update.message.reply_html(message, reply_markup=keyboard)
            
        except ValueError:
            await update.message.reply_html(translate_text("❌ Please enter a valid number.", user_id=user_id))
        return

    # Handle blackjack custom bet input
    if context.user_data.get("bj_custom_bet_pending"):
        pending = context.user_data.pop("bj_custom_bet_pending")
        try:
            bet = int(text)
            if bet < 10:
                await update.message.reply_html(t("bj_min_bet", user_id=user_id))
                return

            balance = get_user_balance(user_id)
            if balance < bet:
                await update.message.reply_html(
                    f"❌ Insufficient balance!\n💰 Your balance: {balance} ⭐"
                )
                return

            if user_id in blackjack_sessions:
                await update.message.reply_html(t("bj_active_game", user_id=user_id))
                return

            await bj_start_game(context, update, user_id, bet)

        except ValueError:
            await update.message.reply_html(
                "❌ Please enter a valid star amount (e.g. <code>150</code>)"
            )
        return

    if context.user_data.get('waiting_for_custom_amount'):
        try:
            amount = int(text)
            if amount < 1:
                await update.message.reply_html(translate_text("❌ Minimum deposit is 1 ⭐", user_id=user_id))
                return
            if amount > 10000:
                await update.message.reply_html(translate_text("❌ Maximum deposit is 10000 ⭐", user_id=user_id))
                return

            context.user_data['waiting_for_custom_amount'] = False
            
            title = f"Deposit {amount} Stars"
            description = f"Add {amount} ⭐ to your game balance"
            payload = f"deposit_{amount}_{user_id}"
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
            await update.message.reply_html(translate_text("❌ Please enter a valid number.", user_id=user_id))
        return
    
    if context.user_data.get('withdraw_state') == 'waiting_amount':
        # Only respond in private chats (DM), not in groups
        if update.effective_chat.type != "private":
            return  # Silently ignore messages in groups
        
        withdraw_type = context.user_data.get('withdraw_type', 'stars')
        
        try:
            if withdraw_type == 'crypto':
                # Crypto withdrawal: accept USD amount and check crypto balance
                try:
                    amount_usd = float(text)
                    min_crypto_usd = 5.0
                    
                    if amount_usd < min_crypto_usd:
                        await update.message.reply_html(
                            f"❌ Minimum withdrawal is ${min_crypto_usd:.0f}"
                        )
                        return
                    
                    # Check crypto balance
                    crypto_balance = lc.user_crypto_balances.get(user_id, 0.0)
                    
                    if amount_usd > crypto_balance:
                        await update.message.reply_html(
                            f"❌ <b>Insufficient crypto balance!</b>\n\n"
                            f"Your crypto balance: <b>${crypto_balance:.2f}</b>\n"
                            f"Requested: <b>${amount_usd:.2f}</b>"
                        )
                        return
                    
                    # Store USD amount for crypto withdrawal
                    context.user_data['withdraw_amount_usd'] = amount_usd
                    context.user_data['withdraw_amount'] = None  # Not using stars
                    context.user_data['withdraw_state'] = 'waiting_address'
                    
                    await update.message.reply_html(
                        f"💎 <b>Withdrawal Amount:</b> ${amount_usd:.2f}\n\n"
                        f"📍 <b>Enter your crypto wallet address:</b>"
                    )
                except ValueError:
                    await update.message.reply_html(translate_text("❌ Please enter a valid number (e.g., 10 or 10.50)"))
            else:
                # Stars withdrawal: accept stars amount
                amount = int(text)
                balance = get_user_balance(user_id)
                
                if amount < lc.MIN_WITHDRAWAL:
                    await update.message.reply_html(t("min_withdrawal_msg", user_id=user_id, min=lc.MIN_WITHDRAWAL))
                    return
                
                if amount > balance:
                    await update.message.reply_html(
                        f"❌ Insufficient balance!\n\n"
                        f"Your balance: {balance} ⭐\n"
                        f"Requested: {amount} ⭐"
                    )
                    return
                
                context.user_data['withdraw_amount'] = amount
                context.user_data['withdraw_amount_usd'] = None
                context.user_data['withdraw_state'] = 'waiting_address'
                
                ton_amount = round(amount * STARS_TO_TON, 8)
                
                await update.message.reply_html(
                    translate_text(
                        f"💎 <b>Withdrawal Amount:</b> {amount} ⭐\n"
                        f"💰 <b>TON Amount:</b> {ton_amount}\n\n"
                        f"📍 <b>Enter your TON wallet address:</b>"
                    )
                )
        except ValueError:
            await update.message.reply_html(translate_text("❌ Please enter a valid number.", user_id=user_id))
        return
    
    if context.user_data.get('withdraw_state') == 'waiting_address':
        # Only respond in private chats (DM), not in groups
        if update.effective_chat.type != "private":
            return  # Silently ignore messages in groups
        
        withdraw_type = context.user_data.get('withdraw_type', 'stars')
        
        if withdraw_type == 'crypto':
            # Crypto withdrawal: validate address
            is_valid, coin_name = is_valid_crypto_address(text)
            
            if not is_valid:
                await update.message.reply_html(
                    f"❌ <b>Invalid crypto address!</b>\n\n"
                    f"Please enter a valid cryptocurrency wallet address.\n\n"
                    f"Supported formats:\n"
                    f"• Bitcoin (1..., 3..., bc1...)\n"
                    f"• Litecoin (L..., M..., ltc1...)\n"
                    f"• Ethereum (0x...)\n"
                    f"• TON (UQ..., EQ...)\n"
                    f"• Solana (base58)\n"
                    f"• Monero (4...)\n"
                    f"• USDT/USDC (0x...)"
                )
                return
            
            context.user_data['withdraw_address'] = text
            context.user_data['detected_coin'] = coin_name
            amount_usd = context.user_data.get('withdraw_amount_usd', 0)
            crypto_balance = lc.user_crypto_balances.get(user_id, 0.0)
            
            keyboard = [
                [
                    InlineKeyboardButton(translate_text("✅ Confirm", user_id=user_id), callback_data="confirm_withdraw"),
                    InlineKeyboardButton(translate_text("❌ Cancel", user_id=user_id), callback_data="cancel_withdraw"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_summary = await update.message.reply_html(
                f"📋 <b>Withdrawal Summary</b>\n\n"
                f"💎 <b>Amount:</b> ${amount_usd:.2f}\n"
                f"💰 <b>Your Crypto Balance:</b> ${crypto_balance:.2f}\n"
                f"🎲 <b>Network:</b> {coin_name}\n"
                f"🏦 <b>Address:</b>\n<code>{text}</code>\n\n"
                f"Please confirm the withdrawal details above.",
                reply_markup=reply_markup
            )
            register_menu_owner(sent_summary, user_id)
        else:
            # Stars withdrawal: validate TON address
            if not is_valid_ton_address(text):
                await update.message.reply_html(
                    f"❌ <b>Invalid TON address!</b>\n\n{translate_text('Please enter a valid TON wallet address.', user_id=user_id)}"
                )
                return
            
            context.user_data['withdraw_address'] = text
            
            stars_amount = context.user_data.get('withdraw_amount', 0)
            ton_amount = round(stars_amount * STARS_TO_TON, 8)
            
            keyboard = [
                [
                    InlineKeyboardButton(translate_text("✅ Confirm", user_id=user_id), callback_data="confirm_withdraw"),
                    InlineKeyboardButton(translate_text("❌ Cancel", user_id=user_id), callback_data="cancel_withdraw"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            sent_summary = await update.message.reply_html(
                translate_text(
                    f"📋 <b>Withdrawal Summary:</b>\n\n"
                    f"⭐ Stars: {stars_amount}\n"
                    f"💎 TON: {ton_amount}\n"
                    f"🏦 Address: <code>{text}</code>\n\n"
                    f"Confirm withdrawal?"
                ),
                reply_markup=reply_markup
            )
            register_menu_owner(sent_summary, user_id)
        return
