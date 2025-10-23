Muhsaib Charitable Foundation Bot - Full package (API-free)
Files:
  - muhsaibcf_bot_full.py  (main bot)
  - requirements.txt
  - Procfile
  - .env.example
Instructions:
  1. Set BOT_TOKEN in Railway variables.
  2. Upload files to your GitHub repo and connect Railway.
  3. Ensure data.csv is in the repo root. If missing, bot will create it.
  4. Deploy and check logs. For errors paste the red traceback here.
Admin commands:
  /adduser FullName|Email|Phone|AdmissionNumber|Course|Address
  /create_poll Title | opt1,opt2 | minutes_from_now
  /post_results <poll_id>
  /credit <row_index> <amount>
