#!/usr/bin/env python3
# muhsaibcf_bot.py
# Compatible with python-telegram-bot v20+
# Features: CSV read/write (data.csv), verification (email+phone), 24h sessions,
# 7-day edit window from bot start, English/Hausa, deep-translator, admin commands,
# auto-create missing files/folders, backups, reminders, and railway-friendly.

import os
import json
import asyncio
import shutil
import logging
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional

import pandas as pd
from deep_translator import GoogleTranslator

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ---------------- Config ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")  # set this in Railway variables
CSV_PATH = os.getenv("CSV_PATH", "data.csv")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")
LOG_DIR = os.getenv("LOG_DIR", "logs")
BOT_START_FILE = os.getenv("BOT_START_FILE", "bot_start.json")
SESSIONS_FILE = os.getenv("SESSIONS_FILE", "sessions.json")
USER_LANG_FILE = os.getenv("USER_LANG_FILE", "user_lang.json")
ADMIN_IDS = {7003416998}  # can be updated via env if desired
ADMIN_CONTACT = "@werleedattah | +2349039475752"
BOT_NAME = os.getenv("BOT_NAME", "Muhsaib Charitable Foundation Bot")
EDITABLE_FIELDS = ["FullName", "DateOfBirth", "BankName", "AccountNumber"]
IMMUTABLE_FIELDS = ["Email", "Phone", "AdmissionNumber"]

CSV_POLL_INTERVAL = 8  # seconds

# Ensure directories/files exist
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("muhsaibcf_bot")

# Global state (loaded in memory)
_df: Optional[pd.DataFrame] = None
_csv_mtime: Optional[float] = None
_sessions = {}  # chat_id -> {verified, index, verified_until, editing_field}
_user_lang = {}  # chat_id -> 'en'/'ha'

# ---------------- i18n ----------------
STRINGS = {
    "en": {
        "welcome": "👋 Welcome to *Muhsaib Charitable Foundation Bot!*\\nYou can view or update your registered student information here.\\nSupport: {support}\\n\\nWhich language? Reply 'en' or 'ha'.",
        "ask_email": "Please enter your *email* to begin verification:",
        "ask_phone": "Now send your *phone number* (include country code, e.g. 234...):",
        "not_found": "❌ No matching record found for that email + phone. Please try /start again or contact support.",
        "verified": "✅ Verification successful. You are verified for 24 hours.",
        "menu_edit_note": "🗓️ Days left to edit: {left} (editing allowed: {allowed})\\n🔔 Note: after editing, it may take up to 7 days to reflect on the school portal.",
        "menu_buttons": ["✏️ Edit info", "🔄 Refresh", "❌ Logout"],
        "choose_field": "Which field would you like to edit?",
        "send_new_value": "Please send the new value for *{field}*",
        "updated_success": "*{field}* updated from:\\n`{old}`\\n to:\\n`{new}`\\n\\n🔔 Note: changes may take up to 7 days to reflect.",
        "editing_disabled": "⚠️ Editing is currently disabled. The editing window has closed.",
        "not_verified": "You must /start and verify first.",
        "logout": "You have been logged out. Use /start to verify again.",
        "admin_only": "You are not authorized for this command.",
        "backup_done": "Backup created: {path}",
        "backup_failed": "Backup failed.",
        "csv_reloaded": "CSV reloaded from disk.",
        "rows_count": "Rows in CSV: {n}\\nDays since start: {days}\\nDays left to edit: {left}",
        "find_usage": "Usage: /find <email_or_phone>",
        "no_results": "No results found.",
        "lang_set": "Language set to {lang}.",
    },
    "ha": {
        "welcome": "👋 Barka da zuwa *Muhsaib Charitable Foundation Bot!*\\nZa ka iya duba ko sabunta bayanan rajista.\\nTaimako: {support}\\n\\nAmsa 'en' ko 'ha'.",
        "ask_email": "Don Allah shigar da *email* dinka:",
        "ask_phone": "Aiko da *lambar waya* (misali 234...):",
        "not_found": "❌ Ba a sami bayanin da ya dace ba. Gwada /start ko tuntubi admin.",
        "verified": "✅ An tabbatar. Za ku kasance an tabbatar na awanni 24.",
        "menu_edit_note": "🗓️ Kwanaki suka rage: {left} (an yarda: {allowed})\\n🔔 Lura: bayan gyara, zai iya ɗaukar har zuwa kwanaki 7 kafin ya bayyana.",
        "menu_buttons": ["✏️ Gyara bayanai", "🔄 Sabunta", "❌ Fita"],
        "choose_field": "Wane filin kake son gyarawa?",
        "send_new_value": "Don Allah aiko sabon ƙima don *{field}*",
        "updated_success": "*{field}* an sabunta daga:\\n`{old}`\\n zuwa:\\n`{new}`\\n\\n🔔 Lura: zai iya ɗaukar har zuwa kwanaki 7.",
        "editing_disabled": "⚠️ An rufe lokacin gyara yanzu.",
        "not_verified": "Dole ne ka /start ka tabbatar da kanka.",
        "logout": "An fita daga asusun. Yi amfani da /start domin sake tabbatarwa.",
        "admin_only": "❌ Ba kai bane admin.",
        "backup_done": "An ƙirƙiri madadin: {path}",
        "backup_failed": "Madadin ya kasa.",
        "csv_reloaded": "An sabunta CSV daga faifai.",
        "rows_count": "Layuka a CSV: {n}\\nKwanaki tun an fara: {days}\\nKwanaki suka rage: {left}",
        "find_usage": "Amfani: /find <email_ko_phone>",
        "no_results": "Babu sakamako.",
        "lang_set": "An saita harshe zuwa {lang}.",
    },
}

DEFAULT_LANG = "en"


def tr(chat_id, key, **kwargs):
    lang = _user_lang.get(str(chat_id), DEFAULT_LANG)
    text = STRINGS.get(lang, STRINGS[DEFAULT_LANG]).get(key, "")
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


# ---------------- Bot start date & editing window ----------------
def ensure_bot_start_date():
    if os.path.exists(BOT_START_FILE):
        try:
            with open(BOT_START_FILE, "r", encoding="utf-8") as f:
                j = json.load(f)
                return datetime.fromisoformat(j["start_date"])
        except Exception:
            logger.exception("Failed reading bot_start.json; creating new start date")
    sd = datetime.utcnow()
    with open(BOT_START_FILE, "w", encoding="utf-8") as f:
        json.dump({"start_date": sd.isoformat()}, f)
    return sd


BOT_START_DATE = ensure_bot_start_date()


def days_since_start():
    return (datetime.utcnow() - BOT_START_DATE).days + 1


def days_left_to_edit():
    return max(0, 7 - (days_since_start() - 1))


def editing_allowed():
    return days_since_start() <= 7


# ---------------- Persistence helpers ----------------
def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        logger.exception("Failed saving json")


def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Failed loading json")
    return {}


def save_sessions():
    save_json(SESSIONS_FILE, _sessions)


def load_sessions():
    global _sessions
    _sessions = load_json(SESSIONS_FILE) or {}


def save_user_lang():
    save_json(USER_LANG_FILE, _user_lang)


def load_user_lang():
    global _user_lang
    _user_lang = load_json(USER_LANG_FILE) or {}


# ---------------- CSV load/save ----------------
async def github_download(github_url: str, dest: str):
    if not github_url:
        return
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(github_url, timeout=12) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open(dest, "wb") as f:
                        f.write(content)
                    logger.info("Downloaded CSV from GitHub")
    except Exception:
        logger.exception("Failed GitHub CSV download")


def load_csv():
    global _df, _csv_mtime
    try:
        if not os.path.exists(CSV_PATH):
            logger.info("CSV not found; creating empty CSV with headers")
            df = pd.DataFrame(columns=EDITABLE_FIELDS + IMMUTABLE_FIELDS + ["Timestamp", "Course", "Address", "Name"])
            df.to_csv(CSV_PATH, index=False)
            _df = df
            _csv_mtime = os.path.getmtime(CSV_PATH)
            return
        mtime = os.path.getmtime(CSV_PATH)
        if _csv_mtime is None or mtime != _csv_mtime:
            df = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)
            for c in EDITABLE_FIELDS + IMMUTABLE_FIELDS:
                if c not in df.columns:
                    df[c] = ""
            _df = df
            _csv_mtime = mtime
            logger.info("CSV loaded: %d rows", _df.shape[0])
    except Exception:
        logger.exception("load_csv failed")
        _df = pd.DataFrame(columns=EDITABLE_FIELDS + IMMUTABLE_FIELDS)


def backup_csv(reason="manual"):
    try:
        ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
        dest = os.path.join(BACKUP_DIR, f"data_{ts}.csv")
        if os.path.exists(CSV_PATH):
            shutil.copy2(CSV_PATH, dest)
            logger.info("Backup created: %s", dest)
            return dest
    except Exception:
        logger.exception("backup failed")
    return None


def save_csv_with_backup(reason="edit"):
    global _df
    try:
        if os.path.exists(CSV_PATH):
            backup_csv(reason)
        tmp = CSV_PATH + ".tmp"
        _df.to_csv(tmp, index=False)
        os.replace(tmp, CSV_PATH)
        logger.info("CSV saved")
        return True
    except Exception:
        logger.exception("save_csv failed")
        return False


# ---------------- Data lookup ----------------
def find_user_row(email: str, phone: str):
    if _df is None or _df.empty:
        return None, None
    mask = (_df["Email"].astype(str).str.strip().str.lower() == str(email).strip().lower()) & (
        _df["Phone"].astype(str).str.strip() == str(phone).strip()
    )
    matches = _df[mask]
    if matches.empty:
        return None, None
    idx = matches.index[0]
    return idx, _df.loc[idx]


def format_user_record(row: pd.Series) -> str:
    order = ["FullName", "Email", "Phone", "AdmissionNumber", "Course", "Address", "DateOfBirth", "BankName", "AccountNumber", "Timestamp"]
    parts = []
    for col in order:
        if col in row.index:
            parts.append(f"*{col}*: {row[col]}")
    for col in row.index:
        if col in order:
            continue
        parts.append(f"*{col}*: {row[col]}")
    return "\n".join(parts)


# ---------------- Background tasks ----------------
async def csv_watcher_task(app):
    while True:
        try:
            load_csv()
        except Exception:
            logger.exception("csv_watcher_task error")
        await asyncio.sleep(CSV_POLL_INTERVAL)


async def reminders_task(app):
    bot = app.bot
    while True:
        try:
            left = days_left_to_edit()
            if left in (3, 1):
                for aid in ADMIN_IDS:
                    try:
                        await bot.send_message(chat_id=aid, text=f"⚠️ Reminder: editing window has {left} day(s) left.")
                    except Exception:
                        logger.exception("Failed to send admin reminder")
            if days_since_start() == 8:
                for aid in ADMIN_IDS:
                    try:
                        await bot.send_message(chat_id=aid, text="ℹ️ Editing window has closed (day 8). Users can no longer edit their data.")
                    except Exception:
                        logger.exception("Failed to send admin closure notice")
        except Exception:
            logger.exception("reminders_task error")
        await asyncio.sleep(60 * 60)  # hourly check


# ---------------- Conversation states ----------------
ASK_LANG, ASK_EMAIL, ASK_PHONE, MENU, CHOOSING_FIELD, TYPING_VALUE = range(6)


# ---------------- Decorators ----------------
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **k):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS:
            await update.message.reply_text(tr(update.effective_chat.id, "admin_only"))
            return
        return await func(update, context, *a, **k)
    return wrapper


# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    # greet and ask language preference if not set
    if chat_id not in _user_lang:
        await update.message.reply_text(STRINGS["en"]["welcome"].format(support=ADMIN_CONTACT), parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text("Reply 'en' for English or 'ha' for Hausa")
        return ASK_LANG
    await update.message.reply_text(tr(chat_id, "welcome", support=ADMIN_CONTACT), parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(tr(chat_id, "ask_email"), parse_mode=ParseMode.MARKDOWN)
    _sessions[chat_id] = {"verified": False}
    save_sessions()
    return ASK_EMAIL


async def ask_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip().lower()
    _user_lang[chat_id] = "ha" if text in ("ha", "hausa") else "en"
    save_user_lang()
    await update.message.reply_text(tr(chat_id, "lang_set", lang=_user_lang[chat_id]))
    await update.message.reply_text(tr(chat_id, "ask_email"), parse_mode=ParseMode.MARKDOWN)
    _sessions[chat_id] = {"verified": False}
    save_sessions()
    return ASK_EMAIL


async def ask_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    _sessions[chat_id] = {"verified": False, "email_try": update.message.text.strip()}
    save_sessions()
    await update.message.reply_text(tr(chat_id, "ask_phone"), parse_mode=ParseMode.MARKDOWN)
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    phone = update.message.text.strip()
    email = _sessions.get(chat_id, {}).get("email_try")
    idx, row = await asyncio.to_thread(find_user_row, email, phone)
    if idx is None:
        await update.message.reply_text(tr(chat_id, "not_found"))
        return ConversationHandler.END
    verified_until = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    _sessions[chat_id].update({"verified": True, "index": int(idx), "verified_until": verified_until})
    save_sessions()
    await update.message.reply_text(tr(chat_id, "verified"))
    return await show_user_menu(update, context)


async def show_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # works for both message-based and callback query contexts
    if update.callback_query:
        chat_id = str(update.callback_query.message.chat.id)
        cq = update.callback_query
    else:
        chat_id = str(update.effective_chat.id)
        cq = None
    session = _sessions.get(chat_id)
    if not session or not session.get("verified"):
        if cq:
            await cq.message.reply_text(tr(chat_id, "not_verified"))
        else:
            await update.message.reply_text(tr(chat_id, "not_verified"))
        return ConversationHandler.END
    # check session expiry
    try:
        if datetime.fromisoformat(session["verified_until"]) < datetime.utcnow():
            _sessions.pop(chat_id, None)
            save_sessions()
            if cq:
                await cq.message.reply_text(tr(chat_id, "not_verified"))
            else:
                await update.message.reply_text(tr(chat_id, "not_verified"))
            return ConversationHandler.END
    except Exception:
        _sessions.pop(chat_id, None)
        save_sessions()
        if cq:
            await cq.message.reply_text(tr(chat_id, "not_verified"))
        else:
            await update.message.reply_text(tr(chat_id, "not_verified"))
        return ConversationHandler.END
    idx = session["index"]
    row = _df.loc[int(idx)]
    text = format_user_record(row)
    left = days_left_to_edit()
    allowed = "Yes" if editing_allowed() else "No"
    if _user_lang.get(chat_id) == "ha":
        allowed = "Eh" if editing_allowed() else "A'a"
    edit_note = tr(chat_id, "menu_edit_note", left=left, allowed=allowed)
    kb = [
        [InlineKeyboardButton(tr(chat_id, "menu_buttons")[0], callback_data="edit")],
        [InlineKeyboardButton(tr(chat_id, "menu_buttons")[1], callback_data="refresh"),
         InlineKeyboardButton(tr(chat_id, "menu_buttons")[2], callback_data="logout")]
    ]
    reply_markup = InlineKeyboardMarkup(kb)
    if cq:
        await cq.message.reply_text(text + "\n\n" + edit_note, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text + "\n\n" + edit_note, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    return MENU


async def menu_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    data = query.data
    if data == "edit":
        if not editing_allowed():
            await query.message.reply_text(tr(chat_id, "editing_disabled"))
            return MENU
    ...  # truncated for brevity; full file saved separately
