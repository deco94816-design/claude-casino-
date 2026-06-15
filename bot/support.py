# -*- coding: utf-8 -*-
"""Support / ticket callback handler (support_* callback_data).

Delegated from button_callback. Lifted verbatim except the global-state bridge:
ticket_counter (rebound) plus the user_tickets / user_withdrawals maps via
``lc.*``. Re-imported into librate_casino so button_callback's delegation
resolves unchanged.
"""

from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import (
    db, logger, t, translate_text, handle_errors, format_withdrawal_status,
)


@handle_errors
async def handle_support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all support ticket callbacks"""
    query = update.callback_query
    if not query:
        return
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "support_create_ticket":
        # Ask which bot/topic
        keyboard = [
            [
                InlineKeyboardButton(t("support_withdraw_topic", user_id=user_id), callback_data="support_topic_withdraw"),
                InlineKeyboardButton(t("support_other_topic", user_id=user_id), callback_data="support_topic_other")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "Which bot do you need help with?",
            reply_markup=reply_markup
        )
        await query.answer()
        return
    
    elif data == "support_my_tickets":
        # Show user's tickets
        user_ticket_list = lc.user_tickets.get(user_id, [])
        if not user_ticket_list:
            await query.edit_message_text(
                "🗒 <b>My Tickets</b>\n\n"
                "You don't have any tickets yet.",
                parse_mode=ParseMode.HTML
            )
            await query.answer()
            return
        
        tickets_text = "🗒 <b>My Tickets</b>\n\n"
        for idx, ticket in enumerate(user_ticket_list[-10:], 1):  # Show last 10 tickets
            ticket_id = ticket.get('ticket_id', 'N/A')
            topic = ticket.get('topic', 'Unknown')
            status = ticket.get('status', 'open')
            created = ticket.get('created', '')
            tickets_text += f"{idx}. Ticket #{ticket_id} - {topic} ({status})\n"
        
        await query.edit_message_text(tickets_text, parse_mode=ParseMode.HTML)
        await query.answer()
        return
    
    elif data == "support_topic_withdraw":
        # Show withdrawal history as inline buttons
        buttons = []
        
        # Get all withdrawals for user
        # lc.user_withdrawals structure: {str(user_id): {withdrawal_data}}
        all_withdrawals = []
        
        # Check if user has a withdrawal stored
        user_withdrawal = lc.user_withdrawals.get(str(user_id))
        if user_withdrawal and isinstance(user_withdrawal, dict) and 'exchange_id' in user_withdrawal:
            all_withdrawals.append(user_withdrawal)
        
        # Also check all withdrawals to find ones for this user
        # (in case structure is different or there are multiple)
        for key, withdrawal in lc.user_withdrawals.items():
            if isinstance(withdrawal, dict) and 'exchange_id' in withdrawal:
                # If key is user_id, it's for that user
                try:
                    if int(key) == user_id:
                        if withdrawal not in all_withdrawals:
                            all_withdrawals.append(withdrawal)
                except:
                    pass
        
        # Sort by date (newest first)
        try:
            all_withdrawals.sort(key=lambda x: x.get('created', ''), reverse=True)
        except:
            pass
        
        # Limit to 20 withdrawals for display
        display_withdrawals = all_withdrawals[:20]
        
        if not display_withdrawals:
            await query.edit_message_text(
                "❌ <b>No withdrawals found.</b>\n\n"
                "You don't have any withdrawal history.",
                parse_mode=ParseMode.HTML
            )
            await query.answer()
            return
        
        # Build text and buttons
        page_num = 1
        withdrawal_text = f"Select the exchange you need help with.\nPage {page_num}.\n\n"
        
        for withdrawal in display_withdrawals:
            exchange_id = withdrawal.get('exchange_id', 'N/A')
            stars = withdrawal.get('stars', 0)
            ton_amount = withdrawal.get('ton_amount', 0)
            status = withdrawal.get('status', 'draft')
            created = withdrawal.get('created', '')
            
            status_display = format_withdrawal_status(status)
            
            # Parse date format: "2024-12-07 06:27" -> "07.12 06:27"
            try:
                if isinstance(created, str):
                    if ' ' in created:
                        date_part, time_part = created.split(' ', 1)
                        year, month, day = date_part.split('-')
                        hour, minute = time_part.split(':')[:2]
                        date_display = f"{day}.{month} {hour}:{minute}"
                    else:
                        date_display = created
                else:
                    date_display = str(created)
            except:
                date_display = str(created)
            
            # Format: Two lines per withdrawal
            # Line 1: "Date — Status · Stars → TON · Date"
            # Line 2: "#ExchangeID — Status · Stars → TON · Date"
            withdrawal_text += f"{date_display} — {status_display} · {stars:,} STARS → {ton_amount:.2f} TON · {date_display}\n#{exchange_id} — {status_display} · {stars:,} STARS → {ton_amount:.2f} TON · {date_display}\n"
            
            # Create button for each withdrawal
            button_text = f"#{exchange_id} - {status_display}"
            if len(button_text) > 64:  # Telegram button text limit
                button_text = f"#{exchange_id}"
            buttons.append([InlineKeyboardButton(button_text, callback_data=f"support_withdraw_{exchange_id}")])
        
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.edit_message_text(withdrawal_text, reply_markup=reply_markup)
        await query.answer()
        return
    
    elif data.startswith("support_withdraw_"):
        # User selected a withdrawal
        exchange_id = data.replace("support_withdraw_", "")
        
        # Store selected withdrawal in context
        context.user_data['support_selected_withdrawal'] = exchange_id
        
        keyboard = [
            [InlineKeyboardButton(t("support_issue_frozen", user_id=user_id), callback_data="support_issue_frozen")],
            [InlineKeyboardButton(t("support_issue_locked", user_id=user_id), callback_data="support_issue_locked")],
            [InlineKeyboardButton(t("support_issue_not_received", user_id=user_id), callback_data="support_issue_not_received")],
            [InlineKeyboardButton(t("support_issue_other", user_id=user_id), callback_data="support_issue_other")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "👋 Hello! What seems to be the problem?",
            reply_markup=reply_markup
        )
        await query.answer()
        return
    
    elif data in ["support_issue_frozen", "support_issue_locked", "support_issue_other"]:
        # Create ticket and send wait message
        ticket_id = lc.ticket_counter
        lc.ticket_counter = db.get_ticket_counter() + 1
        db.set_ticket_counter(lc.ticket_counter)
        
        issue_type = {
            "support_issue_frozen": "Transaction frozen",
            "support_issue_locked": "Account locked",
            "support_issue_other": "Another question"
        }.get(data, "Unknown issue")
        
        # Create ticket
        if user_id not in lc.user_tickets:
            lc.user_tickets[user_id] = []
        
        ticket = {
            'ticket_id': ticket_id,
            'user_id': user_id,
            'topic': 'Withdraw',
            'issue': issue_type,
            'withdrawal_id': context.user_data.get('support_selected_withdrawal'),
            'status': 'open',
            'created': datetime.now().isoformat()
        }
        
        lc.user_tickets[user_id].append(ticket)  # Keep in memory for compatibility
        db.add_ticket(
            ticket_id=ticket_id,
            user_id=user_id,
            topic=ticket.get('topic'),
            issue=ticket.get('issue'),
            withdrawal_id=ticket.get('withdrawal_id'),
            status=ticket.get('status', 'open'),
            created=datetime.now()
        )
        
        await query.edit_message_text(
            translate_text("⏳ Please wait—our managers will contact you as soon as possible to resolve your issue.", user_id=user_id)
        )
        await query.answer()
        return
    
    elif data == "support_issue_not_received":
        # Ask how they topped up
        keyboard = [
            [
                InlineKeyboardButton(t("support_topup_fragment", user_id=user_id), callback_data="support_topup_fragment"),
                InlineKeyboardButton(t("support_topup_store", user_id=user_id), callback_data="support_topup_store")
            ],
            [
                InlineKeyboardButton(t("support_topup_premium", user_id=user_id), callback_data="support_topup_premium"),
                InlineKeyboardButton(t("support_topup_gifts", user_id=user_id), callback_data="support_topup_gifts")
            ],
            [
                InlineKeyboardButton(t("support_topup_other_bot", user_id=user_id), callback_data="support_topup_other_bot"),
                InlineKeyboardButton(t("support_topup_other", user_id=user_id), callback_data="support_topup_other")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            translate_text("How did you top up stars to your account?", user_id=user_id),
            reply_markup=reply_markup
        )
        await query.answer()
        return
    
    elif data in ["support_topup_fragment", "support_topup_store", "support_topup_premium", 
                  "support_topup_gifts", "support_topup_other_bot", "support_topup_other"]:
        # All buttons (1-6): Ask for screen recording
        logger.info(f"Support topup callback received: {data} from user {user_id}")
        
        ticket_id = lc.ticket_counter
        lc.ticket_counter = db.get_ticket_counter() + 1
        db.set_ticket_counter(lc.ticket_counter)
        
        topup_method = {
            "support_topup_fragment": "Fragment",
            "support_topup_store": "Apple/Google Store",
            "support_topup_premium": "Premium Bot",
            "support_topup_gifts": "Selling Gifts",
            "support_topup_other_bot": "Purchased in another bot",
            "support_topup_other": "Other"
        }.get(data, "Unknown")
        
        # Create ticket
        if user_id not in lc.user_tickets:
            lc.user_tickets[user_id] = []
        
        ticket = {
            'ticket_id': ticket_id,
            'user_id': user_id,
            'topic': 'Withdraw',
            'issue': "Didn't receive TON",
            'topup_method': topup_method,
            'withdrawal_id': context.user_data.get('support_selected_withdrawal'),
            'status': 'open',
            'waiting_for_video': True,  # Flag to track waiting for video
            'created': datetime.now().isoformat()
        }
        
        lc.user_tickets[user_id].append(ticket)  # Keep in memory for compatibility
        db.add_ticket(
            ticket_id=ticket_id,
            user_id=user_id,
            topic=ticket.get('topic'),
            issue=ticket.get('issue'),
            withdrawal_id=ticket.get('withdrawal_id'),
            status=ticket.get('status', 'open'),
            created=datetime.now()
        )
        
        # Store ticket_id in context for video handler
        context.user_data['support_waiting_video_ticket_id'] = ticket_id
        
        # Answer callback and edit message
        try:
            await query.answer()
            await query.edit_message_text(
                translate_text("Please send a screen recording with all your star transactions.", user_id=user_id)
            )
            logger.info(f"Successfully sent screen recording request for ticket {ticket_id}")
        except Exception as e:
            logger.error(f"Error in support topup handler: {e}", exc_info=True)
            # Try to send as new message if edit fails
            try:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="Please send a screen recording with all your star transactions."
                )
            except Exception as e2:
                logger.error(f"Error sending message for support topup: {e2}", exc_info=True)
        return
    
    elif data == "support_topic_other":
        # Handle other topic
        ticket_id = lc.ticket_counter
        lc.ticket_counter = db.get_ticket_counter() + 1
        db.set_ticket_counter(lc.ticket_counter)
        
        # Create ticket
        if user_id not in lc.user_tickets:
            lc.user_tickets[user_id] = []
        
        ticket = {
            'ticket_id': ticket_id,
            'user_id': user_id,
            'topic': 'Other',
            'status': 'open',
            'created': datetime.now().isoformat()
        }
        
        lc.user_tickets[user_id].append(ticket)  # Keep in memory for compatibility
        db.add_ticket(
            ticket_id=ticket_id,
            user_id=user_id,
            topic=ticket.get('topic'),
            issue=ticket.get('issue'),
            withdrawal_id=ticket.get('withdrawal_id'),
            status=ticket.get('status', 'open'),
            created=datetime.now()
        )
        
        await query.edit_message_text(
            translate_text("⏳ Please wait—our managers will contact you as soon as possible to resolve your issue.", user_id=user_id)
        )
        await query.answer()
        return
