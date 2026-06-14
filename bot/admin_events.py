# -*- coding: utf-8 -*-
"""Admin event commands: /rainevent /jackpot /doubledeposit /tripledeposit
/goldenhour /stopgoldenhour /cashbackevent /stopcashback /eventstatus /stream
/streamoff.

Lifted verbatim from librate_casino. These mutate event-state globals; the
`global X; X=...` rebinds are converted to `lc.X=...` (setattr on the runtime
module) so the single source of truth — read elsewhere via the ModuleState/wallet
bridge — stays consistent. db/helpers imported. Re-imported so main resolves.
"""

from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import librate_casino as lc
from librate_casino import db, handle_errors, is_admin, logger, t, translate_text


@handle_errors
async def rainevent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/rainevent [total_stars] [optional_max_recipients] — distribute stars randomly among active users."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("not_authorized", user_id=user_id))
        return
    if not context.args or len(context.args) < 1:
        await update.message.reply_html(
            "📖 <b>Usage:</b> /rainevent [total_stars] [max_recipients]\n"
            "Example: /rainevent 5000 20"
        )
        return
    try:
        total_stars = int(context.args[0])
        max_recipients = int(context.args[1]) if len(context.args) > 1 else 50
    except ValueError:
        await update.message.reply_html(t("invalid_whole_numbers", user_id=user_id))
        return
    if total_stars <= 0:
        await update.message.reply_html(t("amount_must_be_positive_admin", user_id=user_id))
        return

    # Collect active users (anyone in lc.user_profiles who is not banned)
    candidates = [uid for uid in lc.user_profiles.keys() if not db.is_user_banned(uid) and not is_admin(uid)]
    if not candidates:
        await update.message.reply_html(t("no_eligible_users", user_id=user_id))
        return

    import random as _random
    chosen = _random.sample(candidates, min(max_recipients, len(candidates)))
    share = total_stars // len(chosen)
    if share <= 0:
        await update.message.reply_html(t("amount_too_small", user_id=user_id))
        return

    sent = 0
    for uid in chosen:
        db.adjust_user_balance(uid, share)
        sent += 1
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"🌧️ <b>Rain Event!</b>\n\n"
                    f"You received <b>{share:,} ⭐</b> from the rain!\n"
                    f"Good luck! 🍀"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    await update.message.reply_html(
        f"🌧️ <b>Rain Event Complete!</b>\n\n"
        f"💰 Total distributed: <b>{share * sent:,} ⭐</b>\n"
        f"👥 Recipients: <b>{sent}</b>\n"
        f"⭐ Each received: <b>{share:,} Stars</b>"
    )
    logger.info(f"[RAIN] Admin {user_id} rained {share * sent:,} ⭐ on {sent} users")


@handle_errors
async def jackpot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jackpot [stars] — set a jackpot that the next game winner will claim."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("not_authorized", user_id=user_id))
        return
    if not context.args or len(context.args) < 1:
        status = f"🏆 Active jackpot: <b>{int(lc.active_jackpot_stars):,} ⭐</b>" if lc.active_jackpot_stars > 0 else "No active jackpot."
        await update.message.reply_html(
            f"📖 <b>Usage:</b> /jackpot [stars]\n"
            f"Example: /jackpot 10000\n\n{status}"
        )
        return
    try:
        amount = int(context.args[0])
    except ValueError:
        await update.message.reply_html(t("invalid_amount_plain", user_id=user_id))
        return
    if amount <= 0:
        await update.message.reply_html(t("amount_must_be_positive_admin", user_id=user_id))
        return
    lc.active_jackpot_stars = float(amount)
    await update.message.reply_html(
        f"🏆 <b>Jackpot Set!</b>\n\n"
        f"⭐ Amount: <b>{amount:,} Stars</b>\n"
        f"🎯 The next user to win any game will claim it!"
    )
    logger.info(f"[JACKPOT] Admin {user_id} set jackpot to {amount:,} ⭐")


@handle_errors
async def doubledeposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/doubledeposit — toggle 2x deposit bonus on/off."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("not_authorized", user_id=user_id))
        return
    if lc.deposit_bonus_mult == 2:
        lc.deposit_bonus_mult = 1
        await update.message.reply_html(t("double_deposit_off", user_id=user_id))
        logger.info(f"[EVENT] Admin {user_id} disabled double deposit")
    else:
        lc.deposit_bonus_mult = 2
        await update.message.reply_html(
            "🎁 <b>Double Deposit Bonus: ON!</b>\n\n"
            "All deposits will now receive <b>2x Stars</b>!"
        )
        logger.info(f"[EVENT] Admin {user_id} enabled double deposit")


@handle_errors
async def tripledeposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tripledeposit — toggle 3x deposit bonus on/off."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("not_authorized", user_id=user_id))
        return
    if lc.deposit_bonus_mult == 3:
        lc.deposit_bonus_mult = 1
        await update.message.reply_html(t("triple_deposit_off", user_id=user_id))
        logger.info(f"[EVENT] Admin {user_id} disabled triple deposit")
    else:
        lc.deposit_bonus_mult = 3
        await update.message.reply_html(
            "🎁 <b>Triple Deposit Bonus: ON!</b>\n\n"
            "All deposits will now receive <b>3x Stars</b>!"
        )
        logger.info(f"[EVENT] Admin {user_id} enabled triple deposit")


@handle_errors
async def goldenhour_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/goldenhour [hours] [optional_multiplier] — start a golden hour with boosted win multipliers."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("not_authorized", user_id=user_id))
        return
    if not context.args or len(context.args) < 1:
        status = (
            f"⏰ Active until: {lc.golden_hour_end_dt.strftime('%H:%M:%S')}"
            if lc.golden_hour_end_dt and datetime.now() < lc.golden_hour_end_dt
            else "No active golden hour."
        )
        await update.message.reply_html(
            f"📖 <b>Usage:</b> /goldenhour [hours] [multiplier]\n"
            f"Example: /goldenhour 2 1.5\n\n{status}"
        )
        return
    try:
        hours = float(context.args[0])
        mult  = float(context.args[1]) if len(context.args) > 1 else 1.5
    except ValueError:
        await update.message.reply_html(t("invalid_args_numbers", user_id=user_id))
        return
    if hours <= 0 or mult <= 1:
        await update.message.reply_html(t("hours_must_positive", user_id=user_id))
        return
    lc.golden_hour_end_dt  = datetime.now() + timedelta(hours=hours)
    lc.golden_hour_mult_val = mult
    end_str = lc.golden_hour_end_dt.strftime("%H:%M:%S")
    await update.message.reply_html(
        f"✨ <b>Golden Hour Started!</b>\n\n"
        f"🎯 Multiplier: <b>{mult}x</b> on all game wins\n"
        f"⏰ Duration: <b>{hours}h</b> (until {end_str})\n\n"
        f"All wins are boosted! 🚀"
    )
    logger.info(f"[EVENT] Admin {user_id} started golden hour: {mult}x for {hours}h")


@handle_errors
async def stopgoldenhour_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stopgoldenhour — end golden hour early."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("not_authorized", user_id=user_id))
        return
    if not lc.golden_hour_end_dt or datetime.now() >= lc.golden_hour_end_dt:
        await update.message.reply_html(t("no_active_golden_hour", user_id=user_id))
        return
    lc.golden_hour_end_dt = None
    await update.message.reply_html(t("golden_hour_stopped", user_id=user_id))
    logger.info(f"[EVENT] Admin {user_id} stopped golden hour early")


@handle_errors
async def cashbackevent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cashbackevent [percent] [hours] — refund X% of each loss for X hours."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("not_authorized", user_id=user_id))
        return
    if not context.args or len(context.args) < 2:
        status = (
            f"💸 Active: {lc.cashback_pct}% until {lc.cashback_end_dt.strftime('%H:%M:%S')}"
            if lc.cashback_pct > 0 and lc.cashback_end_dt and datetime.now() < lc.cashback_end_dt
            else "No active cashback event."
        )
        await update.message.reply_html(
            f"📖 <b>Usage:</b> /cashbackevent [percent] [hours]\n"
            f"Example: /cashbackevent 10 3\n\n{status}"
        )
        return
    try:
        pct   = int(context.args[0])
        hours = float(context.args[1])
    except ValueError:
        await update.message.reply_html(t("invalid_args_numbers", user_id=user_id))
        return
    if not 1 <= pct <= 100:
        await update.message.reply_html(t("percent_1_100", user_id=user_id))
        return
    if hours <= 0:
        await update.message.reply_html(t("hours_positive", user_id=user_id))
        return
    lc.cashback_pct       = pct
    lc.cashback_end_dt    = datetime.now() + timedelta(hours=hours)
    lc.cashback_start_dt  = datetime.now()
    lc._cashback_seen_ids = set()
    end_str = lc.cashback_end_dt.strftime("%H:%M:%S")
    await update.message.reply_html(
        f"💸 <b>Cashback Event Started!</b>\n\n"
        f"♻️ Refund: <b>{pct}%</b> of each losing bet\n"
        f"⏰ Duration: <b>{hours}h</b> (until {end_str})\n\n"
        f"Players will receive cashback automatically! 🤑"
    )
    logger.info(f"[EVENT] Admin {user_id} started cashback: {pct}% for {hours}h")


@handle_errors
async def stopcashback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stopcashback — end cashback event early."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(t("not_authorized", user_id=user_id))
        return
    if not lc.cashback_pct:
        await update.message.reply_html(t("no_active_cashback", user_id=user_id))
        return
    lc.cashback_pct      = 0
    lc.cashback_end_dt   = None
    lc.cashback_start_dt = None
    await update.message.reply_html(t("cashback_stopped", user_id=user_id))
    logger.info(f"[EVENT] Admin {update.effective_user.id} stopped cashback early")


@handle_errors
async def eventstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/eventstatus — show all active special events."""
    if not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_html(t("not_authorized", user_id=update.effective_user.id))
        return
    now = datetime.now()
    lines = ["🎪 <b>Active Special Events</b>\n"]

    # Jackpot
    if lc.active_jackpot_stars > 0:
        lines.append(f"🏆 Jackpot: <b>{int(lc.active_jackpot_stars):,} ⭐</b> (waiting for first win)")
    else:
        lines.append("🏆 Jackpot: <i>inactive</i>")

    # Deposit bonus
    if lc.deposit_bonus_mult > 1:
        lines.append(f"🎁 Deposit Bonus: <b>{lc.deposit_bonus_mult}x</b> (active)")
    else:
        lines.append("🎁 Deposit Bonus: <i>inactive</i>")

    # Golden hour
    if lc.golden_hour_end_dt and now < lc.golden_hour_end_dt:
        remaining = lc.golden_hour_end_dt - now
        mins = int(remaining.total_seconds() / 60)
        lines.append(f"✨ Golden Hour: <b>{lc.golden_hour_mult_val}x</b> — {mins} min remaining")
    else:
        lines.append("✨ Golden Hour: <i>inactive</i>")

    # Cashback
    if lc.cashback_pct > 0 and lc.cashback_end_dt and now < lc.cashback_end_dt:
        remaining = lc.cashback_end_dt - now
        mins = int(remaining.total_seconds() / 60)
        lines.append(f"💸 Cashback: <b>{lc.cashback_pct}%</b> — {mins} min remaining")
    else:
        lines.append("💸 Cashback: <i>inactive</i>")

    await update.message.reply_html("\n".join(lines))

async def stream_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enable streaming message effect - admin only"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("âŒ Permission denied", user_id=user_id))
        return
    lc.streaming_enabled = True
    await update.message.reply_html("✅ <b>Streaming ENABLED</b>\n3-5 word chunks, 150ms delays\nUse /streamoff to disable")


async def streamoff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable streaming message effect - admin only"""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_html(translate_text("âŒ Permission denied", user_id=user_id))
        return
    lc.streaming_enabled = False
    await update.message.reply_html("✅ <b>Streaming DISABLED</b> - Normal messages\nUse /stream to enable")
