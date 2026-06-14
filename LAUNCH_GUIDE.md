# Telegram Casino Bot - Setup & Launch Guide

## ✅ Pre-Launch Checklist

### Configuration Complete:
- ✅ Bot token configured in `.env` (`BOT_TOKEN=...`) — never hardcoded in source
- ✅ All Python Modules Installed
- ✅ All Files Syntax Verified
- ✅ Virtual Environment Configured

### System Requirements:
- ✅ Python 3.14.5+
- ✅ Windows/Linux/macOS compatible

---

## 🚀 Launch Instructions

### Option 1: Windows Batch Script (Easiest)
```bash
double-click: run_bot.bat
```
**What it does:**
- Creates virtual environment (if needed)
- Installs dependencies from `requirements.txt`
- Launches the bot via `python -m optimus`

### Option 2: Package entrypoint (canonical)
```bash
python -m optimus
```

### Option 3: Convenience launcher (with dependency pre-flight)
```bash
python run.py
```

> ⚠️ Do **not** run `librate_casino.py` directly. It must be imported as the
> `librate_casino` module (the game modules depend on this); executing it as a
> script now exits with a reminder.

---

## 📊 What Gets Created on First Run

The bot will automatically create these database files:

```
Telegram Desktop/
├── casino_data.db          # User data, balances, game history
├── bot_network.db          # Multi-bot registry & blacklist
├── emoji_mappings.db       # Custom emoji cache
├── templates.db            # Message templates (if used)
└── backups/                # Auto-backup directory
    └── casino_data_*.db    # Timestamped backups
```

---

## 🔧 Bot Configuration

### Admin Users
Currently configured admins:
- Primary: `5709159932`
- Secondary: `8311802199`

To add/modify admins, edit the `admin_list` in the bot code:
```python
admin_list = {ADMIN_ID, 8311802199, YOUR_USER_ID}
```

### Game Settings
Located in `librate_casino.py` (secrets live in `.env` via `optimus/config.py`):
```python
MIN_WITHDRAWAL = 200          # Minimum Stars to withdraw
BONUS_MIN = 30                # Minimum bonus amount
BONUS_MAX = 50                # Maximum bonus amount
CF_MULTIPLIER = 1.92          # Coinflip multiplier
STARS_TO_USD = 0.0179        # Conversion rate
```

### Crypto Currencies Supported
- USDT (TRC20)
- BTC (Bitcoin)
- ETH (Ethereum)
- LTC (Litecoin)
- DOGE (Dogecoin)

Set addresses via bot `/set` command (admin only)

---

## 🎮 Available Games

1. **Dice Battle** - Predict dice roll (1-6)
2. **Darts** - Predict dart score (1-6)
3. **Football** - Predict football outcome (1-5)
4. **Basketball** - Predict shot success (1-5)
5. **Bowling** - Predict pins (0-6)
6. **Mines** - Multi-level mine game
7. **Coinflip** - Heads or Tails (50/50)
8. **Blackjack** - Classic card game
9. **Predict** - Multi-option prediction game

All games use **1.92x multiplier** (house edge built-in)

---

## 👤 User Features

- **Balance Management** - In-app currency (Telegram Stars)
- **Deposits** - Via crypto or Telegram Stars
- **Withdrawals** - Stars or TON
- **Leveling System** - 26 levels with rakeback rewards
- **Referral Program** - Code-based friend referrals
- **Weekly Bonus** - Generated each week
- **Game History** - Complete play records
- **Support Tickets** - Issue reporting

---

## 🛠️ Admin Commands

Access these as admin user:

```
/ban <user_id> <reason>        - Ban user globally
/unban <user_id>               - Remove ban
/wd <amount>                   - Set min withdrawal
/set <coin> <address>          - Set crypto deposit address
/cg <comment>                  - Change gift comment
/steal <data>                  - Set bot identity (name, links)
/video <file_id>               - Set withdrawal video
/stats                         - View bot statistics
/gift <user_id> <amount>       - Send real Stars gift
/pingme                        - Enable gift sending capability
```

---

## 📋 Troubleshooting

### Bot Won't Start
```
Error: "ModuleNotFoundError: No module named 'telegram'"
→ Run: pip install python-telegram-bot httpx Pillow
```

### Database Errors
```
Error: "database is locked"
→ Close any other bot instances
→ Delete *.db-wal and *.db-shm files
→ Restart bot
```

### Connection Issues
```
Error: "Network error when calling Telegram API"
→ Check internet connection
→ Verify bot token is correct
→ Try with different network or proxy
```

### High CPU Usage
```
→ Check if multiple bot instances running
→ Reduce game tick rate in code
→ Switch from SQLite to PostgreSQL for production
```

---

## 🔒 Security Notes

⚠️ **Important:**
- Bot token is sensitive - never share it
- Store in environment variables for production
- Use `.env` file: `BOT_TOKEN=your_token_here`
- Restrict database file permissions
- Enable SQLite encryption for production

### Token Location:
The token is loaded from the `.env` file (gitignored), never stored in source:
```bash
# File: .env
BOT_TOKEN=your_token_here
```

---

## 📈 Monitoring

### View Bot Logs
Logs automatically output to console:
```
2026-05-23 10:30:45 - telegram.bot - INFO - Bot polling started
2026-05-23 10:31:02 - __main__ - INFO - User 123456 placed bet
```

### Check Database
```bash
# View database contents (requires sqlite3)
sqlite3 casino_data.db "SELECT COUNT(*) FROM users;"
```

### Backup Database
```bash
# Manual backup (auto-backup runs every startup)
copy casino_data.db "backups\casino_data_backup.db"
```

---

## 🚪 Stopping the Bot

**Graceful Shutdown:**
- Press `Ctrl + C` in terminal
- Bot will clean up and exit

**Force Stop:**
- Close terminal window (not recommended)
- Use Task Manager → End Process

---

## 📞 Support

For issues or feature requests, refer to:
- [CODEBASE_ANALYSIS.md](CODEBASE_ANALYSIS.md) - Technical documentation
- Admin Support Ticket System - Built into bot

---

## ✨ Next Steps

1. ✅ Start bot with one of the launch methods above
2. Test admin commands in bot chat
3. Configure crypto addresses (`/set` command)
4. Add additional admins if needed
5. Deploy to production server

---

**Bot Ready!** 🎉

Your Telegram Casino Bot is ready to launch. Choose a launch method above and start the application.

Current Status:
- ✅ Configuration: Complete
- ✅ Dependencies: Installed
- ✅ Syntax: Valid
- ✅ Token: Set via `.env`
- ✅ Ready to Run: YES
