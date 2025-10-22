# Muhsaib Charitable Foundation Bot 🤖

This Telegram bot allows students to **view and update their registration information**
stored in `data.csv`. It supports verification, admin controls, backups, logging,
and an automatic 8-day editing window.

## 🚀 Deployment (Railway)

1. Upload these files to Railway.
2. Add Environment Variables:
   - `BOT_TOKEN` = your Telegram bot token
   - `CSV_PATH` = ./data.csv
   - `ADMIN_IDS` = 7003416998
   - `ADMIN_PASS` = optional_admin_password
   - `READ_ONLY` = false
3. Deploy — Railway will auto-detect and start your bot.

## ⚙️ Local Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python muhsaib_bot.py
```

## 📁 Folders Auto-Created
- `backups/` for automatic CSV backups
- `logs/` for action logs
- `bot_start.json` for countdown timer

## 🧩 Features
- User verification (email + phone)
- View & edit information
- Countdown (7-day editing, 8th-day stop)
- Friendly messages with support info
- Admin-only commands: `/reload`, `/backup`, `/stats`, `/all`, `/find`
- Auto-create missing files and folders

## 📞 Support
For help, contact the admin:
- Telegram: [@werleedattah](https://t.me/werleedattah)
- WhatsApp: [Chat Support](https://wa.me/2349039475752?text=I%20need%20help%20with%20Muhsaib%20Charitable%20Foundation%20bot)
