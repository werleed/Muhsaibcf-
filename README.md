Muhsaib Bot v10 - Deployment package
Files:
 - muhsaibcf_bot_final.py  (main bot)
 - requirements.txt
 - Procfile
 - .env.example
 - README.md (this file)

Instructions:
1) Upload all files to your GitHub repo root or upload the zip to Railway.
2) In Railway, set environment variables (Project > Variables):
   BOT_TOKEN (required)
   ADMIN_USERNAME (optional)
   ADMIN_IDS (comma-separated numeric Telegram IDs, include your ID)
   SUPPORT_PHONE (optional)
   CSV_PATH (defaults to data.csv)
3) Ensure your data.csv is at the repo root. The bot will NOT create or overwrite missing CSV.
4) Deploy. Check logs for 'Bot startup complete' and 'CSV loaded' messages.
Admin commands (Telegram):
 - /all                List CSV rows (admin only)
 - /reload             Force reload CSV
 - /broadcast <text>   Send a broadcast to verified users
 - /enable_edit        Enable 7-day edit window from now
 - /disable_edit       Disable editing
User flow:
 - /start -> follow prompts (email, phone) -> view/edit allowed fields for 7 days.
Notes:
 - Edits only update existing columns; no new columns will be created.
 - Backups are saved to backups/ before each save.
