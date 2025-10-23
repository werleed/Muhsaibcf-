#!/usr/bin/env python3
# muhsaibcf_bot_full.py
# Muhsaib Charitable Foundation Bot — Full package (API-free, python-telegram-bot v20.5)
# Features:
# - Verification by Email + Phone using local data.csv
# - Inline menus (user & admin)
# - Report waste (photo optional), request pickups (admin approve/reject/unavailable)
# - Voting/polls created by admin, auto-close, admin posts final results
# - Simulated wallet credited on approved reports (₦500)
# - Admin can add new users to data.csv via /adduser command (no photo)
# - All storage local: data.csv, json files in data folder
# - No external web APIs. Ready for Railway deployment.
import os
import csv
import json
import shutil
import logging
import asyncio
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

import pandas as pd

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ---------- Configuration ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CSV_PATH = os.getenv("CSV_PATH", "data.csv")
DATA_DIR = os.getenv("DATA_DIR", "mcf_data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
LOG_DIR = os.path.join(DATA_DIR, "logs")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
USER_LANG_FILE = os.path.join(DATA_DIR, "user_lang.json")
REPORTS_FILE = os.path.join(DATA_DIR, "reports.json")
PICKUPS_FILE = os.path.join(DATA_DIR, "pickups.json")
POLLS_FILE = os.path.join(DATA_DIR, "polls.json")
BOT_START_FILE = os.path.join(DATA_DIR, "bot_start.json")

# Set admin IDs here (integers)
ADMIN_IDS = {7003416998}
ADMIN_CONTACT = "@werleedattah | +2349039475752"
BOT_NAME = "Muhsaib Charitable Foundation Bot (Full)"

# Fields editable by user
EDITABLE_FIELDS = ["FullName", "DateOfBirth", "BankName", "AccountNumber"]
IMMUTABLE_FIELDS = ["Email", "Phone", "AdmissionNumber"]

CSV_POLL_INTERVAL = 8  # seconds
POLL_CHECK_INTERVAL = 20  # seconds

# ---------- Ensure folders ----------
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("muhsaibcf_full")

# ---------- Global state ----------
_df: Optional[pd.DataFrame] = None
_csv_mtime: Optional[float] = None
_sessions = {}   # chat_id -> session dict
_user_lang = {}  # chat_id -> 'en'/'ha'

# ---------- Local translations ----------
STRINGS = {
    "en": {
        "welcome": "👋 Welcome to *Muhsaib Charitable Foundation*!\\nI can help you view or update your student record and report waste. Support: {support}",
        "ask_lang": "Please reply with your language: 'en' for English or 'ha' for Hausa",
        "ask_email": "Enter your *email* to verify:",
        "ask_phone": "Now send your *phone number* (include country code, e.g. 234...):",
        "verified": "✅ Your account is now verified for 24 hours.",
        "not_found": "⚠️ Record not found. Please contact admin.",
        "menu_edit_note": "🗓️ Days left to edit: {left} (editing allowed: {allowed})",
        "menu_buttons": ["✏️ Edit info", "🗂️ Reports & Pickups", "💰 Wallet", "🗳️ Polls", "⚙️ Settings"],
        "choose_field": "Which field would you like to edit?",
        "send_new_value": "Send the new value for *{field}*:",
        "updated_success": "✅ Updated *{field}* from `{old}` to `{new}`. Changes may take up to 7 days to reflect.",
        "editing_disabled": "⚠️ Editing is disabled. The window has closed.",
        "not_verified": "You must /start and verify first.",
        "logout": "You are logged out. Use /start to verify again.",
        "report_received": "Thank you — your report has been received and sent to admin.",
        "pickup_received": "Your pickup request was submitted. Admin will review.",
        "no_polls": "No active polls at the moment.",
        "vote_ok": "Thank you — your vote has been recorded.",
        "already_voted": "You have already voted in this poll.",
        "adduser_usage": "Usage: /adduser FullName|Email|Phone|AdmissionNumber|Course|Address",
        "adduser_done": "User added to CSV (row index {idx}).",
    },
    "ha": {
        "welcome": "👋 Barka da zuwa *Muhsaib Charitable Foundation*!\\nIna iya taimaka maka duba ko sabunta rajista da bayar da rahoton sharar gida.",
        "ask_lang": "Amsa 'en' ko 'ha' don harshe",
        "ask_email": "Shigar da *email* ɗinka:",
        "ask_phone": "Aiko da *lambar waya* (234...):",
        "verified": "✅ An tabbatar da asusunka na awanni 24.",
        "not_found": "⚠️ Ba a sami rikodin ba. Tuntubi admin.",
        "menu_edit_note": "🗓️ Kwanaki suka rage: {left} (an yarda: {allowed})",
        "menu_buttons": ["✏️ Gyara bayanai", "🗂️ Rahotanni & Pickup", "💰 Aljihun kuɗi", "🗳️ Zabe", "⚙️ Saituna"],
        "choose_field": "Wane filin kake son gyarawa?",
        "send_new_value": "Aiko sabon ƙima don *{field}*:",
        "updated_success": "✅ An sabunta *{field}* daga `{old}` zuwa `{new}`.",
        "editing_disabled": "⚠️ An rufe lokacin gyara.",
        "not_verified": "Dole ne ka /start ka tabbatar.",
        "logout": "An fita. Yi /start don sake shiga.",
        "report_received": "Na gode — an karɓi rahotonka kuma an tura shi zuwa admin.",
        "pickup_received": "An karɓi buƙatar pickup. Admin zai duba.",
        "no_polls": "Babu zabe a yanzu.",
        "vote_ok": "Na gode — an karɓi zabenka.",
        "already_voted": "An riga an jefa zabe.",
        "adduser_usage": "Amfani: /adduser FullName|Email|Phone|AdmissionNumber|Course|Address",
        "adduser_done": "An ƙara mai amfani (layi {idx}).",
    }
}
DEFAULT_LANG = "en"
def tr(chat_id, key, **kwargs):
    lang = _user_lang.get(str(chat_id), DEFAULT_LANG)
    text = STRINGS.get(lang, STRINGS[DEFAULT_LANG]).get(key, "")
    try: return text.format(**kwargs)
    except Exception: return text

# ---------- Start date & edit window ----------
def ensure_start_date():
    if os.path.exists(BOT_START_FILE):
        try:
            with open(BOT_START_FILE, "r", encoding="utf-8") as f:
                j = json.load(f); return datetime.fromisoformat(j["start_date"])
        except Exception: pass
    sd = datetime.utcnow()
    with open(BOT_START_FILE, "w", encoding="utf-8") as f: json.dump({"start_date": sd.isoformat()}, f)
    return sd
BOT_START_DATE = ensure_start_date()
def days_since_start(): return (datetime.utcnow() - BOT_START_DATE).days + 1
def days_left_to_edit(): return max(0, 7 - (days_since_start() - 1))
def editing_allowed(): return days_since_start() <= 7

# ---------- JSON helpers ----------
def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f: json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception: logger.exception("save_json failed: %s", path)

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: logger.exception("load_json failed: %s", path)
    return default

# ---------- CSV load/save ----------
def load_csv():
    global _df, _csv_mtime
    try:
        if not os.path.exists(CSV_PATH):
            # create sample columns
            cols = EDITABLE_FIELDS + IMMUTABLE_FIELDS + ["Course", "Address", "Wallet", "Timestamp"]
            df = pd.DataFrame(columns=cols)
            df.to_csv(CSV_PATH, index=False)
            _df = df; _csv_mtime = os.path.getmtime(CSV_PATH); return
        mtime = os.path.getmtime(CSV_PATH)
        if _csv_mtime is None or mtime != _csv_mtime:
            df = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)
            for c in EDITABLE_FIELDS + IMMUTABLE_FIELDS + ["Wallet"]:
                if c not in df.columns: df[c] = ""
            # ensure wallet numeric
            if "Wallet" in df.columns:
                df["Wallet"] = df["Wallet"].replace("", "0").astype(float)
            _df = df; _csv_mtime = mtime; logger.info("CSV loaded %d rows", _df.shape[0])
    except Exception:
        logger.exception("load_csv failed"); _df = pd.DataFrame(columns=EDITABLE_FIELDS + IMMUTABLE_FIELDS + ["Wallet"])

def save_csv_with_backup(reason="edit"):
    try:
        if os.path.exists(CSV_PATH):
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(CSV_PATH, os.path.join(BACKUP_DIR, f"data_{ts}.csv"))
        tmp = CSV_PATH + ".tmp"; _df.to_csv(tmp, index=False); os.replace(tmp, CSV_PATH); return True
    except Exception:
        logger.exception("save_csv failed"); return False

# ---------- Data lookup ----------
def find_user_row(email: str, phone: str):
    if _df is None or _df.empty: return None, None
    email = (email or "").strip().lower(); phone = (phone or "").strip()
    mask = (_df["Email"].astype(str).str.strip().str.lower() == email) & (_df["Phone"].astype(str).str.strip() == phone)
    matches = _df[mask]
    if matches.empty: return None, None
    idx = matches.index[0]; return int(idx), _df.loc[idx]

def format_user_record(row):
    cols = ["FullName","Email","Phone","AdmissionNumber","Wallet","Course","Address","DateOfBirth","BankName","AccountNumber","Timestamp"]
    lines = []
    for c in cols:
        if c in row.index: lines.append(f"*{c}*: {row[c]}")
    return "\n".join(lines)

# ---------- persistent JSONs ----------
_sessions = load_json(SESSIONS_FILE, {})
_user_lang = load_json(USER_LANG_FILE, {})
_reports = load_json(REPORTS_FILE, [])
_pickups = load_json(PICKUPS_FILE, [])
_polls = load_json(POLLS_FILE, [])

# ---------- Background tasks ----------
async def csv_watcher(app):
    while True:
        try: load_csv()
        except Exception: logger.exception("csv_watcher")
        await asyncio.sleep(CSV_POLL_INTERVAL)

async def poll_checker(app):
    while True:
        try:
            now = datetime.utcnow()
            changed = False
            for poll in _polls:
                if not poll.get("closed") and "ends_at" in poll:
                    ends = datetime.fromisoformat(poll["ends_at"])
                    if now >= ends:
                        poll["closed"] = True; changed = True
                        # notify admins poll closed and results summary
                        total = sum(poll.get("votes", {}).values()) if isinstance(poll.get("votes"), dict) else 0
                        lines = [f"Poll closed: {poll['title']}"]
                        for opt, cnt in poll.get("votes", {}).items(): lines.append(f"{opt}: {cnt}")
                        for aid in ADMIN_IDS:
                            try: await app.bot.send_message(chat_id=aid, text="\n".join(lines))
                            except Exception: logger.exception("notify admin poll closed")
            if changed: save_json(POLLS_FILE, _polls)
        except Exception: logger.exception("poll_checker error")
        await asyncio.sleep(POLL_CHECK_INTERVAL)

# ---------- Conversation states ----------
ASK_LANG, ASK_EMAIL, ASK_PHONE, MENU, CHOOSING_FIELD, TYPING_VALUE, REPORT_PHOTO, REPORT_DETAILS = range(8)

# ---------- Decorators ----------
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **k):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS:
            # reply to whichever is available
            if update.effective_message: await update.effective_message.reply_text("❌ You are not authorized.")
            return
        return await func(update, context, *a, **k)
    return wrapper

# ---------- Handlers ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    await update.message.reply_text(STRINGS["en"]["welcome"].format(support=ADMIN_CONTACT), parse_mode=ParseMode.MARKDOWN_V2)
    await update.message.reply_text(tr(chat_id, "ask_lang"))
    return ASK_LANG

async def ask_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id); t = update.message.text.strip().lower()
    _user_lang[chat_id] = "ha" if t in ("ha","hausa") else "en"; save_json(USER_LANG_FILE, _user_lang)
    await update.message.reply_text(tr(chat_id, "ask_email"), parse_mode=ParseMode.MARKDOWN_V2)
    _sessions[chat_id] = {"verified": False}; save_json(SESSIONS_FILE, _sessions)
    return ASK_EMAIL

async def ask_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id); _sessions[chat_id] = {"verified": False, "email_try": update.message.text.strip()}; save_json(SESSIONS_FILE, _sessions)
    await update.message.reply_text(tr(chat_id, "ask_phone"), parse_mode=ParseMode.MARKDOWN_V2)
    return ASK_PHONE

async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id); phone = update.message.text.strip(); email = _sessions.get(chat_id, {}).get("email_try")
    idx, row = await asyncio.to_thread(find_user_row, email, phone)
    if idx is None:
        await update.message.reply_text(tr(chat_id, "not_found")); return ConversationHandler.END
    # verified for 24 hours
    _sessions[chat_id].update({"verified": True, "index": idx, "verified_until": (datetime.utcnow()+timedelta(hours=24)).isoformat()}); save_json(SESSIONS_FILE, _sessions)
    await update.message.reply_text(tr(chat_id, "verified"))
    return await show_menu(update, context)

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        chat_id = str(update.callback_query.message.chat.id); cq = update.callback_query
    else:
        chat_id = str(update.effective_chat.id); cq = None
    session = _sessions.get(chat_id)
    if not session or not session.get("verified"):
        if cq: await cq.message.reply_text(tr(chat_id, "not_verified"))
        else: await update.message.reply_text(tr(chat_id, "not_verified"))
        return ConversationHandler.END
    idx = session["index"]; row = _df.loc[int(idx)]; text = format_user_record(row)
    left = days_left_to_edit(); allowed = "Yes" if editing_allowed() else "No"
    if _user_lang.get(chat_id) == "ha": allowed = "Eh" if editing_allowed() else "A'a"
    edit_note = tr(chat_id, "menu_edit_note", left=left, allowed=allowed)
    kb = [[InlineKeyboardButton(b, callback_data=f"menu_{i}")] for i,b in enumerate(STRINGS[_user_lang.get(chat_id,DEFAULT_LANG)]["menu_buttons"])]
    kb.append([InlineKeyboardButton("Logout", callback_data="menu_logout")])
    if update.callback_query:
        await update.callback_query.message.reply_text(text + "\n\n" + edit_note, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text + "\n\n" + edit_note, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(kb))
    return MENU

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); chat_id = str(query.message.chat.id)
    data = query.data
    if data == "menu_0":  # Edit info
        buttons = [[InlineKeyboardButton(f, callback_data=f"fld_{f}")] for f in EDITABLE_FIELDS]
        buttons.append([InlineKeyboardButton("Back", callback_data="menu_back")])
        await query.message.reply_text(tr(chat_id,"choose_field"), reply_markup=InlineKeyboardMarkup(buttons)); return CHOOSING_FIELD
    if data == "menu_1":  # Reports & Pickups
        kb = [[InlineKeyboardButton("Report Waste", callback_data="report_new")],
              [InlineKeyboardButton("Request Pickup", callback_data="pickup_new")],
              [InlineKeyboardButton("My Reports", callback_data="my_reports")],
              [InlineKeyboardButton("Back", callback_data="menu_back")]]
        await query.message.reply_text("Reports & Pickups", reply_markup=InlineKeyboardMarkup(kb)); return MENU
    if data == "menu_2":  # Wallet
        idx = _sessions[chat_id]["index"]; bal = _df.at[int(idx),"Wallet"]
        await query.message.reply_text(f"💰 Wallet balance: ₦{bal}"); return MENU
    if data == "menu_3":  # Polls
        active = [p for p in _polls if not p.get("closed")]
        if not active: await query.message.reply_text(tr(chat_id,"no_polls")); return MENU
        for p in active:
            # create vote buttons per option
            kb = [[InlineKeyboardButton(opt, callback_data=f"vote_{p['id']}__{i}") for i,opt in enumerate(p["options"])]]
            await query.message.reply_text(f"*{p['title']}*\n{p.get('desc','')}\nEnds at: {p['ends_at']}", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=InlineKeyboardMarkup(kb))
        return MENU
    if data == "menu_4":  # Settings
        await query.message.reply_text("Settings: not implemented yet."); return MENU
    if data == "menu_logout":
        _sessions.pop(chat_id, None); save_json(SESSIONS_FILE, _sessions); await query.message.reply_text(tr(chat_id,"logout")); return ConversationHandler.END
    if data == "menu_back": return await show_menu(update, context)
    if data == "report_new": await query.message.reply_text("Please send a photo of the waste (or type 'skip')"); return REPORT_PHOTO
    if data == "pickup_new": await query.message.reply_text("Please provide address and short description for pickup:"); return REPORT_DETAILS
    if data == "my_reports":
        chat_idx = _sessions[chat_id]["index"]; my = [r for r in _reports if r["user_index"]==chat_idx]
        if not my: await query.message.reply_text("You have no reports."); return MENU
        for r in my: await query.message.reply_text(json.dumps(r, indent=2))
        return MENU
    return MENU

async def choose_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); chat_id = str(query.message.chat.id)
    data = query.data
    if data.startswith("fld_"):
        field = data.split("fld_",1)[1]
        _sessions[chat_id]["editing_field"] = field; save_json(SESSIONS_FILE, _sessions)
        await query.message.reply_text(tr(chat_id,"send_new_value", field=field), parse_mode=ParseMode.MARKDOWN_V2); return TYPING_VALUE
    return MENU

async def receive_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id); session = _sessions.get(chat_id)
    if not session or not session.get("verified"): await update.message.reply_text(tr(chat_id,"not_verified")); return ConversationHandler.END
    field = session.get("editing_field")
    if not field: await update.message.reply_text("No field selected."); return MENU
    if not editing_allowed(): await update.message.reply_text(tr(chat_id,"editing_disabled")); return MENU
    new = update.message.text.strip(); idx = int(session["index"]); old = _df.at[idx, field]
    _df.at[idx, field] = new; saved = save_csv_with_backup(f"edit_{chat_id}_{field}")
    if saved: await update.message.reply_text(tr(chat_id,"updated_success", field=field, old=old, new=new), parse_mode=ParseMode.MARKDOWN_V2)
    else: await update.message.reply_text("⚠️ Save failed.")
    session.pop("editing_field", None); save_json(SESSIONS_FILE, _sessions); return MENU

async def report_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id); photo = None
    if update.message.photo: photo = update.message.photo[-1].file_id
    else:
        text = update.message.text.strip().lower()
        if text=="skip": photo = None
        else: await update.message.reply_text("Please send a photo or type 'skip'"); return REPORT_PHOTO
    _sessions[chat_id]["report_photo"] = photo; save_json(SESSIONS_FILE, _sessions)
    await update.message.reply_text("Now send address and short description:"); return REPORT_DETAILS

async def report_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id); text = update.message.text.strip(); session = _sessions.get(chat_id)
    if not session or not session.get("verified"): await update.message.reply_text(tr(chat_id,"not_verified")); return ConversationHandler.END
    idx = int(session["index"]); photo = session.pop("report_photo", None)
    rep = {"id": len(_reports)+1, "user_index": idx, "photo": photo, "text": text, "status":"pending", "created_at": datetime.utcnow().isoformat()}
    _reports.append(rep); save_json(REPORTS_FILE, _reports); save_json(SESSIONS_FILE, _sessions)
    # notify admins
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=aid, text=f"New report #{rep['id']} from user row {idx}\\n{text}")
            if photo: await context.bot.send_photo(chat_id=aid, photo=photo)
        except Exception: logger.exception("notify admin report")
    await update.message.reply_text(tr(chat_id,"report_received")); return MENU

async def pickup_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id); text = update.message.text.strip(); session = _sessions.get(chat_id)
    if not session or not session.get("verified"): await update.message.reply_text(tr(chat_id,"not_verified")); return ConversationHandler.END
    idx = int(session["index"])
    p = {"id": len(_pickups)+1, "user_index": idx, "text": text, "status":"pending", "created_at": datetime.utcnow().isoformat()}
    _pickups.append(p); save_json(PICKUPS_FILE, _pickups)
    kb = [[InlineKeyboardButton("Approve", callback_data=f"pickup_approve_{p['id']}"), InlineKeyboardButton("Reject", callback_data=f"pickup_reject_{p['id']}"),
           InlineKeyboardButton("Not in area", callback_data=f"pickup_unavail_{p['id']}")]]
    for aid in ADMIN_IDS:
        try: await context.bot.send_message(chat_id=aid, text=f"Pickup request #{p['id']} from user row {idx}\\n{text}", reply_markup=InlineKeyboardMarkup(kb))
        except Exception: logger.exception("notify admin pickup")
    await update.message.reply_text(tr(chat_id,"pickup_received")); return MENU

async def admin_pickup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    # format: pickup_<action>_<id>
    parts = data.split("_"); action = parts[1]; pid = int(parts[2])
    p = next((x for x in _pickups if x["id"]==pid), None)
    if not p: await query.message.reply_text("Pickup not found"); return
    if action=="approve": p["status"]="approved"
    elif action=="reject": p["status"]="rejected"
    elif action=="unavail": p["status"]="unavailable"
    save_json(PICKUPS_FILE, _pickups)
    # notify user(s) who have session index equal to user_index
    user_idx = p["user_index"]
    for chat, s in _sessions.items():
        if s.get("index")==user_idx:
            try:
                if p["status"]=="approved": await context.bot.send_message(chat_id=int(chat), text="✅ Your pickup was approved by admin.")
                elif p["status"]=="rejected": await context.bot.send_message(chat_id=int(chat), text="❌ Your pickup was rejected by admin.")
                else: await context.bot.send_message(chat_id=int(chat), text="🚫 We don't offer pickups in your area yet. Stay tuned!")
            except Exception: logger.exception("notify user pickup")
    await query.message.reply_text(f"Pickup {pid} marked {p['status']}")

# ---------- Voting / Polls ----------
@admin_only
async def create_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # usage: /create_poll Title | option1,option2 | minutes_from_now
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /create_poll Title | opt1,opt2 | minutes_from_now"); return
    try:
        title_part, opts_part, mins_part = [p.strip() for p in text.split("|")]
        options = [o.strip() for o in opts_part.split(",") if o.strip()]
        mins = int(mins_part)
    except Exception:
        await update.message.reply_text("Bad format. Use: Title | opt1,opt2 | minutes"); return
    pid = len(_polls)+1
    ends_at = (datetime.utcnow() + timedelta(minutes=mins)).isoformat()
    poll = {"id": pid, "title": title_part, "options": options, "votes": {o:0 for o in options}, "voters": {}, "ends_at": ends_at, "closed": False}
    _polls.append(poll); save_json(POLLS_FILE, _polls)
    await update.message.reply_text(f"Poll created (id {pid}). Ends at {ends_at} UTC.")

async def vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); data = query.data
    # data: vote_<pollid>__<opt_index>
    try:
        _, pid_opt = data.split("vote_"); pid, opt_i = pid_opt.split("__")
        pid = int(pid); opt_i = int(opt_i)
    except Exception:
        await query.message.reply_text("Invalid vote"); return
    poll = next((p for p in _polls if p["id"]==pid), None)
    if not poll or poll.get("closed"):
        await query.message.reply_text("Poll not available"); return
    chat = str(query.message.chat.id); session = _sessions.get(chat)
    if not session or not session.get("verified"): await query.message.reply_text(tr(chat,"not_verified")); return
    voter_idx = session["index"]
    if str(voter_idx) in poll.get("voters", {}):
        await query.message.reply_text(tr(chat,"already_voted")); return
    option = poll["options"][opt_i]
    poll["votes"][option] = poll["votes"].get(option,0)+1
    poll["voters"][str(voter_idx)] = option
    save_json(POLLS_FILE, _polls)
    await query.message.reply_text(tr(chat,"vote_ok"))

@admin_only
async def post_poll_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # usage: /post_results <poll_id>
    if not context.args: await update.message.reply_text("Usage: /post_results <poll_id>"); return
    try: pid = int(context.args[0])
    except: await update.message.reply_text("Bad poll id"); return
    poll = next((p for p in _polls if p["id"]==pid), None)
    if not poll: await update.message.reply_text("Poll not found"); return
    total = sum(poll["votes"].values()) if isinstance(poll.get("votes"), dict) else 0
    lines = [f"🗳️ Poll Results: {poll['title']}"]
    for opt, cnt in poll["votes"].items():
        pct = (cnt/total*100) if total>0 else 0
        lines.append(f"{opt}: {cnt} votes ({pct:.1f}%)")
    lines.append(f"Total votes: {total}")
    # post to admin chat (or to a group if needed)
    for aid in ADMIN_IDS:
        try: await context.bot.send_message(chat_id=aid, text="\n".join(lines))
        except Exception: logger.exception("post results")
    await update.message.reply_text("Results posted to admins.")

# ---------- Admin commands ----------
@admin_only
async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _df is None or _df.empty: await update.message.reply_text("CSV empty"); return
    cols = ["FullName","Email","Phone","AdmissionNumber","Wallet"]
    for i,row in _df[cols].iterrows(): await update.message.reply_text(f"{i}: {row.to_dict()}")

@admin_only
async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_csv(); await update.message.reply_text("CSV reloaded")

@admin_only
async def cmd_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _reports: await update.message.reply_text("No reports"); return
    for r in _reports: await update.message.reply_text(json.dumps(r, indent=2))

@admin_only
async def credit_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # usage: /credit <row_index> <amount>
    args = context.args
    if len(args)!=2: await update.message.reply_text("Usage: /credit <row_index> <amount>"); return
    try:
        idx = int(args[0]); amt = float(args[1])
        _df.at[idx, "Wallet"] = float(_df.at[idx, "Wallet"]) + amt; save_csv_with_backup("admin_credit"); await update.message.reply_text("Credited")
    except Exception: await update.message.reply_text("Failed to credit")

@admin_only
async def admin_pickups_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _pickups: await update.message.reply_text("No pickups"); return
    for p in _pickups: await update.message.reply_text(json.dumps(p, indent=2))

@admin_only
async def add_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /adduser FullName|Email|Phone|AdmissionNumber|Course|Address
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(tr(update.effective_chat.id, "adduser_usage")); return
    try:
        parts = [p.strip() for p in " ".join(context.args).split("|")]
        # ensure we have at least FullName, Email, Phone, AdmissionNumber
        while len(parts) < 6: parts.append("")
        fullname, email, phone, adm, course, addr = parts[:6]
        # append to df
        new = {c: "" for c in _df.columns}
        new.update({"FullName": fullname, "Email": email, "Phone": phone, "AdmissionNumber": adm, "Course": course, "Address": addr, "Wallet": 0, "Timestamp": datetime.utcnow().isoformat()})
        global _df
        _df = _df.append(new, ignore_index=True)
        save_csv_with_backup("admin_add_user")
        idx = _df.shape[0]-1
        await update.message.reply_text(tr(update.effective_chat.id, "adduser_done", idx=idx))
    except Exception as e:
        logger.exception("add user failed"); await update.message.reply_text("Failed to add user")

# ---------- Setup & main ----------
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ASK_LANG: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_lang)],
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            MENU: [CallbackQueryHandler(menu_callback, pattern="^menu_")],
            CHOOSING_FIELD: [CallbackQueryHandler(choose_field_callback, pattern="^fld_")],
            TYPING_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_value)],
            REPORT_PHOTO: [MessageHandler(filters.PHOTO | (filters.TEXT & ~filters.COMMAND), report_photo)],
            REPORT_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_details)],
        },
        fallbacks=[CommandHandler("cancel", cmd_start)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    # callbacks
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(choose_field_callback, pattern="^fld_"))
    app.add_handler(CallbackQueryHandler(admin_pickup_handler, pattern="^pickup_"))
    app.add_handler(CallbackQueryHandler(vote_callback, pattern="^vote_"))
    # admin commands
    app.add_handler(CommandHandler("create_poll", create_poll))
    app.add_handler(CommandHandler("post_results", post_poll_results))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CommandHandler("reports", cmd_reports))
    app.add_handler(CommandHandler("credit", credit_user))
    app.add_handler(CommandHandler("pickups", admin_pickups_list))
    app.add_handler(CommandHandler("adduser", add_user_cmd))
    async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Unknown command. Use /start to begin.")
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    return app

async def startup(app):
    load_csv(); logger.info("CSV loaded")
    # ensure json files exist
    save_json(REPORTS_FILE, _reports); save_json(PICKUPS_FILE, _pickups); save_json(POLLS_FILE, _polls)
    app.create_task(csv_watcher(app)); app.create_task(poll_checker(app))
    logger.info(f"{BOT_NAME} started. Days since start: {days_since_start()}")

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN env not set. Exiting."); return
    app = build_app(); app.post_init(startup); app.run_polling()

if __name__ == "__main__": main()
