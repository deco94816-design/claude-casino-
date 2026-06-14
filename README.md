<h1 align="center">🎲 Optimus Casino Bot</h1>

<p align="center">
  A high-performance, asynchronous Telegram Casino bot built with Python, featuring real-time crypto deposits (TON), instant withdrawals, and multiple provably fair games.
</p>

## ✨ Features
- **Integrated Wallet:** Real-time TON deposits powered by OxaPay.
- **Dynamic Conversion:** Live TON-to-USD conversion using the CoinGecko API.
- **Provably Fair Games:** 
  - 🪙 **Coinflip** (PvP and PvB)
  - 🎰 **Slots**
  - 💣 **Mines**
  - 🎲 **Dice & Darts**
- **Admin Dashboard:** Powerful in-app controls for bankroll management, banning, and stats.

## 🚀 Tech Stack
- `python-telegram-bot` (v20+)
- `httpx` (async HTTP requests)
- `sqlite3` (robust file-based storage)

## ▶️ Running
1. Copy `.env.example` to `.env` and set `BOT_TOKEN` (from @BotFather) and admin IDs.
2. Install deps: `pip install -r requirements.txt`
3. Start the bot:

   ```bash
   python -m optimus      # canonical entrypoint
   python run.py          # same, with a dependency pre-flight check
   ```

   > Do **not** run `librate_casino.py` directly — it must be imported as the
   > `librate_casino` module (the game modules depend on this), so it now exits
   > with a reminder if executed as a script.

## 📫 Contact
Created by **[Amex](https://t.me/exiff)**.
