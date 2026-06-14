"""
auto_deposit.py — OxaPay crypto deposit module for optimus-
============================================================
Drop this one file into your optimus- directory.

Then make TWO small changes to "casino v5 (1).py":

    # 1. Add near the top (with the other imports):
    from auto_deposit import setup_deposit_module

    # 2. Add just before application.run_polling():
    setup_deposit_module(application)

That's it.  Register https://your-domain.com/webhook/oxapay in your OxaPay
merchant dashboard so OxaPay can call it.

New pip deps:
    pip install fastapi uvicorn aiohttp
"""

# ── stdlib ────────────────────────────────────────────────────────────────────
import asyncio
import json
import threading
import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ── local (already in optimus-) ───────────────────────────────────────────────
from oxapay import OxaPay      # existing oxapay.py
from storage import db          # existing Database instance


# ═════════════════════════════════════════════════════════════════════════════
# ⚙️  CONFIG  — only section you need to edit
# ═════════════════════════════════════════════════════════════════════════════

import os
from dotenv import load_dotenv

load_dotenv()

OXAPAY_KEY       = os.getenv("OXAPAY_KEY")   # ← loaded from .env
WEBHOOK_PORT     = 8000                          # FastAPI listens here
POLL_INTERVAL    = 30   # seconds between fallback inquiry polls
DEPOSIT_LIFETIME = 60   # minutes an OxaPay address stays live
DEPOSIT_MIN_USD  = 0.10

# Add / remove coins freely — coin + network must match OxaPay's nomenclature
COINS: dict[str, dict] = {
    "BTC":   {"name": "₿ Bitcoin",      "coin": "BTC",   "network": "BTC"},
    "ETH":   {"name": "♦️ Ethereum",     "coin": "ETH",   "network": "ETH"},
    "USDT":  {"name": "₮ USDT",          "coin": "USDT",  "network": "TRC20"},
    "USDC":  {"name": "💲 USDC",          "coin": "USDC",  "network": "BEP20"},
    "LTC":   {"name": "Ł Litecoin",      "coin": "LTC",   "network": "LTC"},
    "SOL":   {"name": "◎ Solana",        "coin": "SOL",   "network": "SOL"},
    "BNB":   {"name": "🔶 BNB",           "coin": "BNB",   "network": "BSC"},
    "TRX":   {"name": "♦️ Tron",          "coin": "TRX",   "network": "TRC20"},
    "XMR":   {"name": "ɱ Monero",        "coin": "XMR",   "network": "XMR"},
    "DAI":   {"name": "🟡 DAI",           "coin": "DAI",   "network": "BEP20"},
    "DOGE":  {"name": "🐶 Dogecoin",      "coin": "DOGE",  "network": "DOGE"},
    "SHIB":  {"name": "🐕 Shiba Inu",     "coin": "SHIB",  "network": "BEP20"},
    "BCH":   {"name": "₿ Bitcoin Cash",  "coin": "BCH",   "network": "BCH"},
    "MATIC": {"name": "♾ Polygon",       "coin": "MATIC", "network": "POLYGON"},
    "TON":   {"name": "💎 Toncoin",       "coin": "TON",   "network": "TON"},
}


# ═════════════════════════════════════════════════════════════════════════════
# 🔗  OXAPAY — reuses the existing oxapay.py already in optimus-
# ═════════════════════════════════════════════════════════════════════════════

_oxapay = OxaPay(merchant_key=OXAPAY_KEY)


# ═════════════════════════════════════════════════════════════════════════════
# 💾  DB SHIM
#     optimus-'s Database class doesn't have get_deposit_by_track_id, so we
#     add one thin helper here.  Everything else uses db directly.
# ═════════════════════════════════════════════════════════════════════════════

def _get_deposit_user_id(track_id: str) -> int | None:
    """Return the user_id that owns this deposit, or None if not found."""
    with db._lock:
        conn = db.get_db_connection()
        row = conn.execute(
            "SELECT user_id FROM deposits WHERE track_id = ?", (track_id,)
        ).fetchone()
        return int(row[0]) if row else None


# ═════════════════════════════════════════════════════════════════════════════
# 🤖  BOT HANDLERS  (python-telegram-bot v21+)
# ═════════════════════════════════════════════════════════════════════════════

def _coin_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row:  list[InlineKeyboardButton]       = []
    for code, info in COINS.items():
        row.append(InlineKeyboardButton(text=info["name"], callback_data=f"dep_{code}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def cmd_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "💰 Select a coin to deposit:", reply_markup=_coin_keyboard(), parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "💰 Select a coin to deposit:", reply_markup=_coin_keyboard(), parse_mode="HTML"
        )


async def cb_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    code = query.data.split("_", 1)[1]
    if code not in COINS:
        await query.edit_message_text("❌ Unknown coin.", parse_mode="HTML")
        return

    info    = COINS[code]
    user_id = query.from_user.id

    await query.edit_message_text(f"⏳ Generating {code} deposit address…", parse_mode="HTML")

    res = await _oxapay.create_deposit_address(info["coin"], info["network"], user_id)
    if not res:
        await query.edit_message_text(
            "❌ Could not generate address. Please try again later.", parse_mode="HTML"
        )
        return
        
    if "error" in res:
        await query.edit_message_text(
            f"❌ Could not generate address.\nError: {res['error']}", parse_mode="HTML"
        )
        return

    # Uses optimus-'s existing db.create_deposit signature
    db.create_deposit(
        user_id    = user_id,
        track_id   = res["trackId"],
        address    = res["address"],
        currency   = info["coin"],
        amount_usd = DEPOSIT_MIN_USD,
    )

    coin_name = info["name"].split(" ", 1)[-1] if " " in info["name"] else info["name"]
    user_name = query.from_user.first_name.replace('<', '&lt;').replace('>', '&gt;')
    
    caption = (
        f"Deposit {coin_name}\n"
        f"⚠️ The address belongs only to user {user_name}\n\n"
        f"Coin: {coin_name}\n"
        f"Network: {res['network']}\n\n"
        f"🔁 Transfer address: <code>{res['address']}</code>\n\n"
        f"💎 To top up your balance, send the desired amount to the specified address.\n"
        f"ℹ️ Minimum deposit amount is {DEPOSIT_MIN_USD}$"
    )
    
    await query.edit_message_text(
        text       = caption,
        parse_mode = "HTML",
    )


# ═════════════════════════════════════════════════════════════════════════════
# 💸  DEPOSIT STATE MACHINE
#     Single function called by both the webhook AND the polling job.
#     Uses db methods that already exist in optimus-'s storage.py.
# ═════════════════════════════════════════════════════════════════════════════

_ton_price_cache = {"price": 0.0, "time": 0.0}

async def get_ton_price_usd() -> float | None:
    current_time = asyncio.get_event_loop().time()
    if current_time - _ton_price_cache["time"] < 60 and _ton_price_cache["price"] > 0:
        return _ton_price_cache["price"]

    url = "https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                data = await resp.json()
                price = float(data.get("the-open-network", {}).get("usd", 0.0))
                if price > 0:
                    _ton_price_cache["price"] = price
                    _ton_price_cache["time"] = current_time
                return price
    except Exception as e:
        print(f"[auto_deposit] Error fetching TON price: {e}", flush=True)
        return _ton_price_cache["price"] if _ton_price_cache["price"] > 0 else None


async def process_deposit(bot: Bot, track_id: str, status: str, pay_amount: float) -> None:
    if pay_amount <= 0:
        print(f"[auto_deposit] Aborting deposit {track_id} because pay_amount is <= 0: {pay_amount}", flush=True)
        return

    """
    Called by BOTH the webhook and the polling job when a deposit state changes.
    Only takes action once (storage checks prevent double-crediting).
    """
    status_title = status.title()
    
    if status_title == "Paid":
        # 1. Prevent double crediting
        if db.deposit_already_credited(track_id):
            return

        user_id = _get_deposit_user_id(track_id)
        if user_id is None:
            print(f"[auto_deposit] Paid webhook for unknown track_id={track_id}")
            return

        # Fetch live TON price
        ton_price = await get_ton_price_usd()
        if not ton_price:
            print(f"[auto_deposit] Aborting deposit {track_id} because TON price could not be fetched.", flush=True)
            return

        stars = int(pay_amount / (ton_price / 200))

        # Uses optimus-'s existing storage methods
        db.mark_deposit_paid(track_id, pay_amount)
        db.adjust_user_balance(user_id, stars)
        await _notify_user(bot, user_id, pay_amount, stars)

    elif status_title in ("Expired", "Cancelled", "Failed"):
        db.mark_deposit_expired(track_id, status_title.lower())

    # "Confirming" requires no action — we wait for "Paid"


async def _notify_user(bot: Bot, user_id: int, amount: float, stars: int) -> None:
    text = (
        "✅ <b>Deposit Converted!</b>\n\n"
        f"💵 Deposit confirmed: <b>${amount:.2f}</b>\n"
        f"⭐ Stars Credited: <b>{stars}</b> ⭐"
    )
    try:
        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
    except Exception as exc:
        print(f"[auto_deposit] Notify failed for user {user_id}: {exc}")


# ═════════════════════════════════════════════════════════════════════════════
# ⏱️  POLLING JOB
#     Runs on PTB's built-in job queue — no raw asyncio tasks.
#     Catches deposits whose webhook was delayed or missed.
# ═════════════════════════════════════════════════════════════════════════════

async def _poll_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fallback: inquire OxaPay for every pending deposit and update status.
    Fires every POLL_INTERVAL seconds.
    """
    try:
        pending = db.get_pending_deposits()   # [{track_id, user_id, ...}, ...]
        for dep in pending:
            result = await _oxapay.inquiry_deposit(dep["track_id"])
            if not result or result.get("result") != 100:
                continue

            remote_status = result.get("status", "")
            remote_status_title = remote_status.title()
            
            try:
                pay_amount = float(result.get("pay_amount", result.get("payAmount", 0)))
            except (ValueError, TypeError):
                pay_amount = 0

            if pay_amount <= 0:
                continue

            if remote_status_title in ("Paid", "Expired", "Cancelled", "Failed"):
                await process_deposit(context.bot, dep["track_id"], remote_status, pay_amount)

    except Exception as exc:
        print(f"[auto_deposit] Poll error: {exc}")


# ═════════════════════════════════════════════════════════════════════════════
# 🌐  FASTAPI WEBHOOK
# ═════════════════════════════════════════════════════════════════════════════

webhook_app = FastAPI()

# These are set by _init_job once the bot's event loop is running
_bot_ref:  Bot                            | None = None
_bot_loop: asyncio.AbstractEventLoop     | None = None


@webhook_app.post("/webhook/oxapay")
async def oxapay_webhook(request: Request):
    """
    OxaPay calls this URL whenever a deposit status changes.
    Register it in your OxaPay merchant dashboard.
    """
    body = (await request.body()).decode("utf-8")
    sig  = request.headers.get("hmac", "")

    if not sig:
        raise HTTPException(status_code=400, detail="Missing HMAC")

    # Reuses the static method from optimus-'s existing oxapay.py
    if not OxaPay.verify_webhook_signature(body, sig, OXAPAY_KEY):
        raise HTTPException(status_code=400, detail="Invalid HMAC signature")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    track_id   = str(data.get("trackId", ""))
    status     = data.get("status", "")
    
    try:
        pay_amount = float(data.get("pay_amount", data.get("payAmount", 0)))
    except (ValueError, TypeError):
        pay_amount = 0

    if pay_amount <= 0:
        return {"status": "ok"}

    # Bridge from uvicorn's thread → bot's asyncio event loop
    if _bot_ref and _bot_loop:
        asyncio.run_coroutine_threadsafe(
            process_deposit(_bot_ref, track_id, status, pay_amount),
            _bot_loop,
        )
    else:
        # Bot loop not ready yet — the fallback poll job will catch it
        print(f"[auto_deposit] Webhook received before loop ready: {track_id} {status}")

    return {"status": "ok"}


# ═════════════════════════════════════════════════════════════════════════════
# 🔌  PUBLIC ENTRY POINT
#     Call setup_deposit_module(application) in casino v5 (1).py before
#     application.run_polling().  That's all you need to do.
# ═════════════════════════════════════════════════════════════════════════════

def setup_deposit_module(application: Application) -> None:
    """
    Wire everything into the existing PTB Application in two steps:

    Step 1 — Register Telegram handlers + polling job (happens now).
    Step 2 — Capture bot event loop + start FastAPI (happens 3 sec after
              run_polling() starts, via a run_once job).

    Usage:
        from auto_deposit import setup_deposit_module
        ...
        setup_deposit_module(application)
        application.run_polling()
    """

    # ── 1. Bot handlers ───────────────────────────────────────────────────────
    application.add_handler(CallbackQueryHandler(cmd_deposit, pattern=r"^crypto_deposit$"))
    application.add_handler(
        CallbackQueryHandler(cb_deposit, pattern=r"^dep_")
    )

    # ── 2. Recurring poll job ─────────────────────────────────────────────────
    application.job_queue.run_repeating(
        callback = _poll_job,
        interval = POLL_INTERVAL,
        first    = POLL_INTERVAL,  # first run after one full interval
        name     = "oxapay_deposit_poll",
    )

    # ── 3. Init job: capture event loop + start uvicorn ───────────────────────
    async def _init_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
        global _bot_ref, _bot_loop
        _bot_ref  = ctx.bot
        _bot_loop = asyncio.get_running_loop()

        def _run_uvicorn() -> None:
            uvicorn.run(
                webhook_app,
                host      = "0.0.0.0",
                port      = WEBHOOK_PORT,
                log_level = "warning",
            )

        t = threading.Thread(target=_run_uvicorn, daemon=True)
        t.start()
        print(f"[auto_deposit] Webhook server listening on :{WEBHOOK_PORT}")

    application.job_queue.run_once(_init_job, when=3, name="oxapay_init")
