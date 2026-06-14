# -*- coding: utf-8 -*-
"""Economy & access admin commands: balance (/addbal /removebal /setbal /resetbal
/transferbal /topbal /totalbal), freeze (/freeze /unfreeze), admin roster
(/addadmin /removeadmin /listadmins) and bans (/ban /unban).

Lifted verbatim from librate_casino. Globals rebound by load_data (admin_list,
banned_users, frozen_users, username_to_id) are referenced live via lc.* to stay
current after startup load; other helpers imported. Re-imported so main resolves.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import (
    handle_errors, is_admin, is_frozen, db, t, translate_text,
    adjust_user_balance, set_user_balance, get_user_balance, get_user_link,
    save_data, ADMIN_ID, ADMIN_BALANCE, logger,
)


@handle_errors
async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    
    if not context.args or len(context.args) == 0:
        await update.message.reply_html(
            "👑 <b>Add Admin</b>\n\n"
            "Usage: /addadmin [user_id]\n"
            "Example: /addadmin 123456789\n\n"
            f"Current admins: {len(lc.admin_list)}"
        )
        return
    
    try:
        new_admin_id = int(context.args[0])
        
        if new_admin_id in lc.admin_list:
            await update.message.reply_html(translate_text(f"⚠️  User <code>{new_admin_id}</code> is already an admin!", user_id=user_id))
            return
        
        lc.admin_list.add(new_admin_id)
        user_balances[new_admin_id] = ADMIN_BALANCE
        db.add_admin(new_admin_id)
        save_data()
        
        await update.message.reply_html(
            translate_text(
                f"✅ <b>New admin added successfully!</b>\n\n"
                f"👤 User ID: <code>{new_admin_id}</code>\n"
                f"💰 Balance: <b>{ADMIN_BALANCE:,} ⭐</b>\n"
                f"👑 Total admins: {len(lc.admin_list)}"
            )
        )
        
        logger.info(f"Admin {user_id} added new admin: {new_admin_id}")
        
    except ValueError:
        await update.message.reply_html(translate_text("❌ Invalid user ID! Please enter a valid number.", user_id=user_id))


@handle_errors
async def addbal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add balance to user (admin only)"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    
    if not context.args or len(context.args) == 0:
        await update.message.reply_html(
            "💰 <b>Add Balance</b>\n\n"
            "Usage: /addbal [user_id/@username] [amount]\n"
            "Example: /addbal 123456789 1000\n"
            "Example: /addbal @username 500\n\n"
            "After sending the command, you'll choose Stars or Crypto."
        )
        return
    
    # Parse arguments
    target_arg = context.args[0]
    amount_arg = context.args[1] if len(context.args) > 1 else None
    
    # Resolve user_id from username or chat_id
    target_user_id = None
    target_username = None
    
    # Check if it's a username (starts with @)
    if target_arg.startswith('@'):
        target_username = target_arg[1:]
        target_user_id = lc.username_to_id.get(target_username.lower())
        if not target_user_id:
            await update.message.reply_html(
                f"❌ <b>User not found!</b>\n\n"
                f"Username: @{target_username}\n\n"
                f"The user must have interacted with the bot first."
            )
            return
    else:
        # Try to parse as user_id
        try:
            target_user_id = int(target_arg)
        except ValueError:
            await update.message.reply_html(translate_text("❌ Invalid user ID or username!", user_id=user_id))
            return
    
    # Get amount if provided
    if amount_arg:
        try:
            amount = float(amount_arg)
            if amount <= 0:
                await update.message.reply_html(translate_text("❌ Amount must be greater than 0!", user_id=user_id))
                return
            
            # Store in context for callback
            context.user_data['addbal_target_id'] = target_user_id
            context.user_data['addbal_amount'] = amount
            context.user_data['addbal_username'] = target_username
            
            # Show buttons to choose Stars or Crypto
            # Use string formatting to ensure proper decimal handling
            amount_str = str(amount).replace('.', 'DOT')  # Replace . with DOT to avoid callback data issues
            keyboard = [
                [
                    InlineKeyboardButton(t("btn_stars_dep", user_id=user_id), callback_data=f"addbal_stars_{target_user_id}_{amount_str}"),
                    InlineKeyboardButton(t("btn_crypto_dep", user_id=user_id), callback_data=f"addbal_crypto_{target_user_id}_{amount_str}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            username_text = f"📛 Username: @{target_username}\n" if target_username else ""
            await update.message.reply_html(
                f"💰 <b>Add Balance</b>\n\n"
                f"👤 User ID: <code>{target_user_id}</code>\n"
                f"{username_text}"
                f"💵 Amount: {amount}\n\n"
                f"Choose balance type:",
                reply_markup=reply_markup
            )
        except ValueError:
            await update.message.reply_html(translate_text("❌ Invalid amount! Please enter a valid number.", user_id=user_id))
    else:
        # No amount provided, ask for it
        context.user_data['addbal_target_id'] = target_user_id
        context.user_data['addbal_username'] = target_username
        context.user_data['waiting_for_addbal_amount'] = True
        
        await update.message.reply_html(
            f"💰 <b>Add Balance</b>\n\n"
            f"👤 User ID: <code>{target_user_id}</code>\n"
            f"📛 Username: @{target_username}" if target_username else f"👤 User ID: <code>{target_user_id}</code>\n\n"
            f"💫 <b>Enter the amount to add:</b>"
        )


@handle_errors
async def removebal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove balance from user (admin only)"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_html(
            "💸 <b>Remove Balance</b>\n\n"
            "Usage: /removebal [user_id/@username] [amount]\n"
            "Example: /removebal 123456789 500\n"
            "Example: /removebal @username 200"
        )
        return
    target_arg = context.args[0]
    target_user_id = None
    target_username = None
    if target_arg.startswith('@'):
        target_username = target_arg[1:]
        target_user_id = lc.username_to_id.get(target_username.lower())
        if not target_user_id:
            await update.message.reply_html(t("user_not_found_user", user_id=user_id, username=target_username))
            return
    else:
        try:
            target_user_id = int(target_arg)
        except ValueError:
            await update.message.reply_html(t("invalid_user_id_or_username", user_id=user_id))
            return
    try:
        amount = float(context.args[1])
        if amount <= 0:
            await update.message.reply_html(t("amount_must_be_positive", user_id=user_id))
            return
    except ValueError:
        await update.message.reply_html(t("invalid_amount", user_id=user_id).rstrip("."))
        return
    if is_admin(target_user_id):
        await update.message.reply_html(t("cannot_modify_admin_balance", user_id=user_id))
        return
    current_balance = db.get_user_balance(target_user_id)
    if amount > current_balance:
        amount = current_balance  # Cap at current balance
    db.adjust_user_balance(target_user_id, -amount)
    new_balance = db.get_user_balance(target_user_id)
    user_balances[target_user_id] = new_balance
    username_display = f"@{target_username}" if target_username else f"<code>{target_user_id}</code>"
    await update.message.reply_html(
        f"💸 <b>Balance Removed!</b>\n\n"
        f"👤 User: {username_display}\n"
        f"➖ Removed: <b>{amount:,.0f} ⭐</b>\n"
        f"💰 New Balance: <b>{new_balance:,.0f} ⭐</b>"
    )
    logger.info(f"Admin {user_id} removed {amount} from user {target_user_id}")


@handle_errors
async def setbal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set user balance to exact amount (admin only)"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_html(
            "💰 <b>Set Balance</b>\n\n"
            "Usage: /setbal [user_id/@username] [amount]\n"
            "Example: /setbal 123456789 1000\n"
            "Example: /setbal @username 500"
        )
        return
    target_arg = context.args[0]
    target_user_id = None
    target_username = None
    if target_arg.startswith('@'):
        target_username = target_arg[1:]
        target_user_id = lc.username_to_id.get(target_username.lower())
        if not target_user_id:
            await update.message.reply_html(t("user_not_found_user", user_id=user_id, username=target_username))
            return
    else:
        try:
            target_user_id = int(target_arg)
        except ValueError:
            await update.message.reply_html(t("invalid_user_id_or_username", user_id=user_id))
            return
    try:
        amount = float(context.args[1])
        if amount < 0:
            await update.message.reply_html(t("amount_negative", user_id=user_id))
            return
    except ValueError:
        await update.message.reply_html(t("invalid_amount", user_id=user_id).rstrip("."))
        return
    if is_admin(target_user_id):
        await update.message.reply_html(t("cannot_modify_admin_balance", user_id=user_id))
        return
    old_balance = db.get_user_balance(target_user_id)
    db.set_user_balance(target_user_id, amount)
    user_balances[target_user_id] = amount
    username_display = f"@{target_username}" if target_username else f"<code>{target_user_id}</code>"
    await update.message.reply_html(
        f"💰 <b>Balance Set!</b>\n\n"
        f"👤 User: {username_display}\n"
        f"📊 Old Balance: <b>{old_balance:,.0f} ⭐</b>\n"
        f"💰 New Balance: <b>{amount:,.0f} ⭐</b>"
    )
    logger.info(f"Admin {user_id} set balance of user {target_user_id} to {amount}")


@handle_errors
async def resetbal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset user balance to zero (admin only)"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    if not context.args or len(context.args) < 1:
        await update.message.reply_html(
            "🔄 <b>Reset Balance</b>\n\n"
            "Usage: /resetbal [user_id/@username]\n"
            "Example: /resetbal 123456789\n"
            "Example: /resetbal @username"
        )
        return
    target_arg = context.args[0]
    target_user_id = None
    target_username = None
    if target_arg.startswith('@'):
        target_username = target_arg[1:]
        target_user_id = lc.username_to_id.get(target_username.lower())
        if not target_user_id:
            await update.message.reply_html(t("user_not_found_user", user_id=user_id, username=target_username))
            return
    else:
        try:
            target_user_id = int(target_arg)
        except ValueError:
            await update.message.reply_html(t("invalid_user_id_or_username", user_id=user_id))
            return
    if is_admin(target_user_id):
        await update.message.reply_html(t("cannot_modify_admin_balance", user_id=user_id))
        return
    old_balance = db.get_user_balance(target_user_id)
    db.set_user_balance(target_user_id, 0)
    user_balances[target_user_id] = 0
    username_display = f"@{target_username}" if target_username else f"<code>{target_user_id}</code>"
    await update.message.reply_html(
        f"🔄 <b>Balance Reset!</b>\n\n"
        f"👤 User: {username_display}\n"
        f"📊 Old Balance: <b>{old_balance:,.0f} ⭐</b>\n"
        f"💰 New Balance: <b>0 ⭐</b>"
    )
    logger.info(f"Admin {user_id} reset balance of user {target_user_id} (was {old_balance})")


@handle_errors
async def transferbal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Transfer balance between two users (admin only)"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    if not context.args or len(context.args) < 3:
        await update.message.reply_html(
            "🔄 <b>Transfer Balance</b>\n\n"
            "Usage: /transferbal [from_user] [to_user] [amount]\n"
            "Example: /transferbal 123456789 987654321 500\n"
            "Example: /transferbal @user1 @user2 1000"
        )
        return

    def resolve_user(arg):
        if arg.startswith('@'):
            uname = arg[1:]
            uid = lc.username_to_id.get(uname.lower())
            return uid, uname
        try:
            return int(arg), None
        except ValueError:
            return None, None

    from_id, from_username = resolve_user(context.args[0])
    to_id, to_username = resolve_user(context.args[1])
    if not from_id:
        await update.message.reply_html(t("src_user_not_found", user_id=user_id, arg=context.args[0]))
        return
    if not to_id:
        await update.message.reply_html(t("dst_user_not_found", user_id=user_id, arg=context.args[1]))
        return
    if from_id == to_id:
        await update.message.reply_html(t("cannot_transfer_same_user", user_id=user_id))
        return
    try:
        amount = float(context.args[2])
        if amount <= 0:
            await update.message.reply_html(t("amount_must_be_positive", user_id=user_id))
            return
    except ValueError:
        await update.message.reply_html(t("invalid_amount", user_id=user_id).rstrip("."))
        return
    if is_admin(from_id) or is_admin(to_id):
        await update.message.reply_html(t("cannot_transfer_admin", user_id=user_id))
        return
    from_balance = db.get_user_balance(from_id)
    if amount > from_balance:
        await update.message.reply_html(
            f"❌ <b>Insufficient balance!</b>\n\n"
            f"Source user balance: <b>{from_balance:,.0f} ⭐</b>\n"
            f"Requested transfer: <b>{amount:,.0f} ⭐</b>"
        )
        return
    db.adjust_user_balance(from_id, -amount)
    db.adjust_user_balance(to_id, amount)
    new_from = db.get_user_balance(from_id)
    new_to = db.get_user_balance(to_id)
    user_balances[from_id] = new_from
    user_balances[to_id] = new_to
    from_display = f"@{from_username}" if from_username else f"<code>{from_id}</code>"
    to_display = f"@{to_username}" if to_username else f"<code>{to_id}</code>"
    await update.message.reply_html(
        f"🔄 <b>Balance Transferred!</b>\n\n"
        f"📤 From: {from_display} → <b>{new_from:,.0f} ⭐</b>\n"
        f"📥 To: {to_display} → <b>{new_to:,.0f} ⭐</b>\n"
        f"💰 Amount: <b>{amount:,.0f} ⭐</b>"
    )
    logger.info(f"Admin {user_id} transferred {amount} from {from_id} to {to_id}")


@handle_errors
async def topbal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top 10 users by balance (admin only)"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    # Fetch more than 10 in case some are admins we need to filter out
    top_users = db.get_top_balances(20)
    # Filter out admins (they have fake unlimited balance)
    top_users = [(uid, bal) for uid, bal in top_users if not is_admin(uid)][:10]
    if not top_users:
        await update.message.reply_html(t("no_users_with_balance", user_id=user_id))
        return
    lines = []
    for i, (uid, balance) in enumerate(top_users, 1):
        # Try to find username
        uname = None
        for name, mapped_id in lc.username_to_id.items():
            if mapped_id == uid:
                uname = name
                break
        display = f"@{uname}" if uname else f"<code>{uid}</code>"
        frozen_tag = " 🧊" if is_frozen(uid) else ""
        lines.append(f"{i}. {display} — <b>{balance:,.0f} ⭐</b>{frozen_tag}")
    text = "🏆 <b>Top 10 Balances</b>\n\n" + "\n".join(lines)
    await update.message.reply_html(text)


@handle_errors
async def totalbal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show total balance across all users (admin only)"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    total = db.get_total_balance()
    conn = db.get_db_connection()
    user_count = conn.execute("SELECT COUNT(*) as cnt FROM users WHERE balance > 0").fetchone()['cnt']
    await update.message.reply_html(
        f"💰 <b>Total Balance Across All Users</b>\n\n"
        f"📊 Total: <b>{total:,.0f} ⭐</b>\n"
        f"👥 Users with balance: <b>{user_count}</b>"
    )


@handle_errors
async def freeze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Freeze a user's balance (admin only)"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    target_user_id = None
    target_username = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = update.message.reply_to_message.from_user.id
        target_username = update.message.reply_to_message.from_user.username or update.message.reply_to_message.from_user.first_name
    elif context.args and len(context.args) > 0:
        arg = context.args[0].strip()
        if arg.startswith('@'):
            arg = arg[1:]
        if arg.lower() in lc.username_to_id:
            target_user_id = lc.username_to_id[arg.lower()]
            target_username = arg
        else:
            try:
                target_user_id = int(arg)
            except ValueError:
                await update.message.reply_html(
                    "❌ <b>Invalid input!</b>\n\n"
                    "Usage: /freeze [user_id/@username] or reply to a message"
                )
                return
    else:
        await update.message.reply_html(
            "🧊 <b>Freeze User</b>\n\n"
            "Usage:\n"
            "• /freeze [user_id]\n"
            "• /freeze @username\n"
            "• /freeze (reply to user's message)\n\n"
            "Frozen users cannot deposit, withdraw, or play."
        )
        return
    if not target_user_id:
        await update.message.reply_html(t("user_not_found", user_id=user_id))
        return
    if is_admin(target_user_id):
        await update.message.reply_html(t("cannot_freeze_admin", user_id=user_id))
        return
    if target_user_id in lc.frozen_users:
        await update.message.reply_html(t("user_already_frozen", user_id=user_id))
        return
    lc.frozen_users.add(target_user_id)
    db.set_frozen_users(lc.frozen_users)
    balance = get_user_balance(target_user_id)
    username_display = f"@{target_username}" if target_username else f"<code>{target_user_id}</code>"
    await update.message.reply_html(
        f"🧊 <b>User Frozen!</b>\n\n"
        f"👤 User: {username_display}\n"
        f"💰 Frozen Balance: <b>{balance:,.0f} ⭐</b>\n\n"
        f"This user can no longer deposit, withdraw, or play."
    )
    logger.info(f"Admin {user_id} froze user {target_user_id}")


@handle_errors
async def unfreeze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unfreeze a user's balance (admin only)"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    target_user_id = None
    target_username = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = update.message.reply_to_message.from_user.id
        target_username = update.message.reply_to_message.from_user.username or update.message.reply_to_message.from_user.first_name
    elif context.args and len(context.args) > 0:
        arg = context.args[0].strip()
        if arg.startswith('@'):
            arg = arg[1:]
        if arg.lower() in lc.username_to_id:
            target_user_id = lc.username_to_id[arg.lower()]
            target_username = arg
        else:
            try:
                target_user_id = int(arg)
            except ValueError:
                await update.message.reply_html(
                    "❌ <b>Invalid input!</b>\n\n"
                    "Usage: /unfreeze [user_id/@username] or reply to a message"
                )
                return
    else:
        await update.message.reply_html(
            "🔥 <b>Unfreeze User</b>\n\n"
            "Usage:\n"
            "• /unfreeze [user_id]\n"
            "• /unfreeze @username\n"
            "• /unfreeze (reply to user's message)"
        )
        return
    if not target_user_id:
        await update.message.reply_html(t("user_not_found", user_id=user_id))
        return
    if target_user_id not in lc.frozen_users:
        await update.message.reply_html(t("user_not_frozen", user_id=user_id))
        return
    lc.frozen_users.discard(target_user_id)
    db.set_frozen_users(lc.frozen_users)
    username_display = f"@{target_username}" if target_username else f"<code>{target_user_id}</code>"
    await update.message.reply_html(
        f"🔥 <b>User Unfrozen!</b>\n\n"
        f"👤 User: {username_display}\n\n"
        f"This user can now deposit, withdraw, and play again."
    )
    logger.info(f"Admin {user_id} unfroze user {target_user_id}")


@handle_errors
async def removeadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    
    if not context.args or len(context.args) == 0:
        await update.message.reply_html(
            translate_text(
                "👑 <b>Remove Admin</b>\n\n"
                "Usage: /removeadmin [user_id]\n"
                "Example: /removeadmin 123456789"
            )
        )
        return
    
    try:
        remove_admin_id = int(context.args[0])
        
        if remove_admin_id == ADMIN_ID:
            await update.message.reply_html(translate_text("❌ Cannot remove the main admin!", user_id=user_id))
            return
        
        if remove_admin_id not in lc.admin_list:
            await update.message.reply_html(translate_text(f"⚠️  User <code>{remove_admin_id}</code> is not an admin!", user_id=user_id))
            return
        
        lc.admin_list.remove(remove_admin_id)
        db.remove_admin(remove_admin_id)
        save_data()
        
        await update.message.reply_html(
            translate_text(
                f"✅ <b>Admin removed successfully!</b>\n\n"
                f"👤 User ID: <code>{remove_admin_id}</code>\n"
                f"👑 Remaining admins: {len(lc.admin_list)}"
            )
        )
        
        logger.info(f"Admin {user_id} removed admin: {remove_admin_id}")
        
    except ValueError:
        await update.message.reply_html(translate_text("❌ Invalid user ID! Please enter a valid number.", user_id=user_id))


@handle_errors
async def listadmins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    
    admin_text = "👑 <b>Admin List</b>\n\n"
    admin_text += f"Total admins: {len(lc.admin_list)}\n\n"
    
    for idx, admin_id in enumerate(lc.admin_list, 1):
        is_main = " (Main Admin)" if admin_id == ADMIN_ID else ""
        admin_text += f"{idx}. <code>{admin_id}</code>{is_main}\n"
    
    await update.message.reply_html(admin_text)


@handle_errors
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban a user - bot will ignore them"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    
    target_user_id = None
    target_username = None
    
    # Check if replying to a message
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = update.message.reply_to_message.from_user.id
        target_username = update.message.reply_to_message.from_user.username or update.message.reply_to_message.from_user.first_name
    # Check if username or user_id provided as argument
    elif context.args and len(context.args) > 0:
        arg = context.args[0].strip()
        # Remove @ if present
        if arg.startswith('@'):
            arg = arg[1:]
        
        # Try to find user by username
        if arg.lower() in lc.username_to_id:
            target_user_id = lc.username_to_id[arg.lower()]
            target_username = arg
        # Try to parse as user_id
        else:
            try:
                target_user_id = int(arg)
            except ValueError:
                await update.message.reply_html(
                    translate_text(
                        "❌ <b>Invalid input!</b>\n\n"
                        "Usage:\n"
                        "• /ban [user_id]\n"
                        "• /ban @username\n"
                        "• /ban (reply to user's message)"
                    )
                )
                return
    else:
        await update.message.reply_html(
            translate_text(
                "🔨 <b>Ban User</b>\n\n"
                "Usage:\n"
                "• /ban [user_id]\n"
                "• /ban @username\n"
                "• /ban (reply to user's message)\n\n"
                "Example: /ban 123456789 or /ban @username",
                user_id=user_id
            )
        )
        return
    
    if not target_user_id:
        await update.message.reply_html(translate_text("❌ <b>User not found!</b>", user_id=user_id))
        return
    
    # Prevent banning admins
    if is_admin(target_user_id):
        await update.message.reply_html(translate_text("❌ <b>Cannot ban an admin!</b>", user_id=user_id))
        return
    
    # Check if already banned
    if target_user_id in lc.banned_users:
        await update.message.reply_html(
            translate_text(
                f"⚠️  <b>User is already banned!</b>\n\n"
                f"👤 User ID: <code>{target_user_id}</code>\n"
                f"📛 Username: @{target_username}" if target_username else f"👤 User ID: <code>{target_user_id}</code>"
            )
        )
        return
    
    # Ban the user
    lc.banned_users.add(target_user_id)
    db.set_user_banned(target_user_id, True)
    save_data()
    
    # Get user link
    user_link = get_user_link(target_user_id, target_username or f"User {target_user_id}")
    
    await update.message.reply_html(
        translate_text(f"Another one bites the {user_link}..!Banned", user_id=user_id)
    )
    
    logger.info(f"Admin {user_id} banned user: {target_user_id} ({target_username})")


@handle_errors
async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban a user - bot will listen to them again"""
    if not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("❌ <b>You don't have permission to use this command.</b>", user_id=user_id))
        return
    
    target_user_id = None
    target_username = None
    
    # Check if replying to a message
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = update.message.reply_to_message.from_user.id
        target_username = update.message.reply_to_message.from_user.username or update.message.reply_to_message.from_user.first_name
    # Check if username or user_id provided as argument
    elif context.args and len(context.args) > 0:
        arg = context.args[0].strip()
        # Remove @ if present
        if arg.startswith('@'):
            arg = arg[1:]
        
        # Try to find user by username
        if arg.lower() in lc.username_to_id:
            target_user_id = lc.username_to_id[arg.lower()]
            target_username = arg
        # Try to parse as user_id
        else:
            try:
                target_user_id = int(arg)
            except ValueError:
                await update.message.reply_html(
                    translate_text(
                        "❌ <b>Invalid input!</b>\n\n"
                        "Usage:\n"
                        "• /unban [user_id]\n"
                        "• /unban @username\n"
                        "• /unban (reply to user's message)"
                    )
                )
                return
    else:
        await update.message.reply_html(
            translate_text(
                "✅ <b>Unban User</b>\n\n"
                "Usage:\n"
                "• /unban [user_id]\n"
                "• /unban @username\n"
                "• /unban (reply to user's message)\n\n"
                "Example: /unban 123456789 or /unban @username"
            )
        )
        return
    
    if not target_user_id:
        await update.message.reply_html(translate_text("❌ <b>User not found!</b>", user_id=user_id))
        return
    
    # Check if user is banned
    if target_user_id not in lc.banned_users:
        await update.message.reply_html(
            f"⚠️  <b>User is not banned!</b>\n\n"
            f"👤 User ID: <code>{target_user_id}</code>\n"
            f"📛 Username: @{target_username}" if target_username else f"👤 User ID: <code>{target_user_id}</code>"
        )
        return
    
    # Unban the user
    lc.banned_users.discard(target_user_id)
    db.set_user_banned(target_user_id, False)
    save_data()
    
    username_display = f"@{target_username}" if target_username else "No username"
    await update.message.reply_html(
        translate_text(
            f"✅ <b>User unbanned successfully!</b>\n\n"
            f"👤 User ID: <code>{target_user_id}</code>\n"
            f"📛 Username: {username_display}\n\n"
            f"The bot will now listen to this user again."
        )
    )
    
    logger.info(f"Admin {user_id} unbanned user: {target_user_id} ({target_username})")
