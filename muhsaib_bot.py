# muhsaib_bot.py
"""
Muhsaib Charitable Foundation Bot - Final Implementation
Features:
- Local CSV read/write (data.csv in same folder)
- Multi-language (English + Hausa) with per-user preference persisted
- Verify by Email + Phone (matches a row in data.csv)
- Verification session valid for 24 hours
- Editable fields: FullName, DateOfBirth, BankName, AccountNumber
- Admin commands: /all, /reload, /stats, /backup, /find
- Auto backups, action logging, error logging
- 7-day editing window from bot start; day 8 editing locked automatically
- CSV watcher that reloads when file changes
- Optional GitHub CSV download via GITHUB_CSV_URL
- Uses python-telegram-bot v13 (sync)
"""

import os
import json
import time
import shutil
import logging
import threading
from datetime import datetime, timedelta
from functools import wraps

import requests
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, Update
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters,
    CallbackQueryHandler, ConversationHandler, CallbackContext
)

# ---------------- Config (environment variables) ----------------
BOT_TOKEN = os.getenv('BOT_TOKEN')
CSV_PATH = os.getenv('CSV_PATH', './data.csv')
GITHUB_CSV_URL = os.getenv('GITHUB_CSV_URL', '')  # optional raw link
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '7003416998').split(',') if x.strip().isdigit()}
ADMIN_PASS = os.getenv('ADMIN_PASS', '')  # optional
READ_ONLY = os.getenv('READ_ONLY', 'false').lower() in ('1', 'true', 'yes')
START_DATE_OVERRIDE = os.getenv('START_DATE', '')  # optional ISO date override
BOT_NAME = os.getenv('BOT_NAME', 'Muhsaib Charitable Foundation Bot')
SUPPORT_LINK = os.getenv('SUPPORT_LINK', 'https://wa.me/2349039475752?text=I%20need%20help%20with%20muhsaib%20charitable%20foundation%20bot')

# Behavior tuning
CSV_POLL_INTERVAL = 8  # seconds to poll file mtime
BACKUP_DIR = './backups'
LOG_DIR = './logs'
BOT_START_FILE = './bot_start.json'
SESSIONS_FILE = './sessions.json'
USER_LANG_FILE = './user_lang.json'
ACTION_LOG_FILE = os.path.join(LOG_DIR, 'actions.log')
ERROR_LOG_FILE = os.path.join(LOG_DIR, 'errors.log')

# Editable and immutable fields
EDITABLE_FIELDS = ['FullName', 'DateOfBirth', 'BankName', 'AccountNumber']
IMMUTABLE_FIELDS = ['Email', 'Phone', 'AdmissionNumber']

# Ensure directories exist
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('muhsaib_bot')

# global data
_data_lock = threading.RLock()
_df = None
_csv_mtime = None
_stop_event = threading.Event()

# sessions: chat_id -> {verified:bool, index:int, verified_until:iso}
_sessions = {}
# user languages
_user_lang = {}  # chat_id -> 'en'/'ha'

# ---------------- Internationalization (English + Hausa) ----------------
STRINGS = {
    'en': {
        'welcome': "👋 Welcome to *Muhsaib Charitable Foundation Bot!* \nYou can view or update your registered student information here.\nFor support contact: {support}\n\nWhich language do you prefer? /language",
        'ask_email': "Please enter your *email* to begin verification:",
        'ask_phone': "Thank you. Now please send your *phone number* (include country code, e.g. 234...):",
        'not_found': "❌ No matching record found for that email + phone. Please try /start again or contact support.",
        'verified': "✅ Verification successful. You are verified for 24 hours.",
        'menu_edit_note': "🗓️ Days left to edit: {left} (editing allowed: {allowed})\n🔔 Note: after you edit, changes may take up to 7 days to reflect on the school portal.",
        'menu_buttons': ['✏️ Edit info', '🔄 Refresh', '❌ Logout'],
        'choose_field': "Which field would you like to edit?",
        'send_new_value': "Please send the new value for *{field}*",
        'updated_success': "*{field}* updated from:\n`{old}`\n to:\n`{new}`\n\n🔔 Please note: updates may take up to 7 days to reflect on the school portal.",
        'editing_disabled': "⚠️ Editing is currently disabled. The editing window has closed.",
        'not_verified': "You must /start and verify first.",
        'logout': "You have been logged out. Use /start to verify again.",
        'admin_only': "❌ You are not authorized for this command.",
        'backup_done': "Backup created: {path}",
        'backup_failed': "Backup failed.",
        'csv_reloaded': "CSV reloaded from disk.",
        'rows_count': "Rows in CSV: {n}\nDays since start: {days}\nDays left to edit: {left}",
        'find_usage': "Usage: /find <email_or_phone>",
        'no_results': "No results found.",
        'start_lang_prompt': "Choose language / Zabi harshe (English / Hausa):\nReply 'en' for English or 'ha' for Hausa",
        'lang_set': "Language set to {lang}.",
    },
    'ha': {
        'welcome': "👋 Barka da zuwa *Muhsaib Charitable Foundation Bot!* \nZa ka iya duba ko sabunta bayanan rajistar ka anan.\nDomin taimako tuntubi: {support}\n\nWane yare kake so? /language",
        'ask_email': "Don Allah shigar da *email* dinka don tabbatarwa:",
        'ask_phone': "Na gode. Yanzu aiko da *lambar waya* (ciki har da lambar ƙasa, misali 234...):",
        'not_found': "❌ Ba a sami bayanin da ya dace ba. Don Allah gwada /start ko tuntubi admin.",
        'verified': "✅ An tabbatar da ku. Za ku kasance an tabbatar na awanni 24.",
        'menu_edit_note': "🗓️ Yawan kwanaki da suka rage don gyara: {left} (an yarda gyara: {allowed})\n🔔 Lura: bayan gyara, zai iya ɗaukar har zuwa kwanaki 7 kafin ya bayyana a gidan yanar makaranta.",
        'menu_buttons': ['✏️ Gyara bayanai', '🔄 Sabunta', '❌ Fita'],
        'choose_field': "Wane filin kake son gyarawa?",
        'send_new_value': "Don Allah aiko sabon ƙima don *{field}*",
        'updated_success': "*{field}* an sabunta daga:\n`{old}`\n zuwa:\n`{new}`\n\n🔔 Lura: gyare-gyare na iya ɗaukar har zuwa kwanaki 7 kafin su bayyana.",
        'editing_disabled': "⚠️ An rufe lokacin gyara yanzu.",
        'not_verified': "Dole ne ka fara (/start) ka tabbatar da kanka.",
        'logout': "An fice daga asusu. Yi amfani da /start domin sake tabbatarwa.",
        'admin_only': "❌ Ba kai bane admin.",
        'backup_done': "An ƙirƙiri madadin: {path}",
        'backup_failed': "Madadin ya kasa.",
        'csv_reloaded': "An sabunta CSV daga faifai.",
        'rows_count': "Layuka a CSV: {n}\nKwanaki tun an fara: {days}\nKwanaki suka rage: {left}",
        'find_usage': "Amfani: /find <email_ko_phone>",
        'no_results': "Babu sakamako.",
        'start_lang_prompt': "Zabi harshe / Choose language (Hausa / English):\nAmsa 'ha' don Hausa ko 'en' don English",
        'lang_set': "An saita yare zuwa {lang}.",
    }
}

# language fallback
DEFAULT_LANG = 'en'

def tr(chat_id, key, **kwargs):
    lang = _user_lang.get(str(chat_id), DEFAULT_LANG)
    text = STRINGS.get(lang, STRINGS[DEFAULT_LANG]).get(key, '')
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text

# ---------------- Bot start date & editing window ----------------
def ensure_bot_start_date():
    if START_DATE_OVERRIDE:
        try:
            return datetime.fromisoformat(START_DATE_OVERRIDE)
        except Exception:
            logger.warning('Invalid START_DATE format; ignoring')
    if os.path.exists(BOT_START_FILE):
        try:
            with open(BOT_START_FILE, 'r') as f:
                j = json.load(f)
                return datetime.fromisoformat(j['start_date'])
        except Exception:
            logger.exception('Failed to read bot_start.json')
    sd = datetime.utcnow()
    with open(BOT_START_FILE, 'w') as f:
        json.dump({'start_date': sd.isoformat()}, f)
    return sd

BOT_START_DATE = ensure_bot_start_date()

def days_since_start():
    delta = datetime.utcnow() - BOT_START_DATE
    return delta.days + 1

def days_left_to_edit():
    left = max(0, 7 - (days_since_start() - 1))
    return left

def editing_allowed():
    if READ_ONLY:
        return False
    return days_since_start() <= 7

# ---------------- Persistence helpers ----------------
def save_sessions():
    try:
        with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_sessions, f, ensure_ascii=False)
    except Exception:
        logger.exception('Failed to save sessions')

def load_sessions():
    global _sessions
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, 'r', encoding='utf-8') as f:
                _sessions = json.load(f)
        except Exception:
            logger.exception('Failed to load sessions')
            _sessions = {}

def save_user_lang():
    try:
        with open(USER_LANG_FILE, 'w', encoding='utf-8') as f:
            json.dump(_user_lang, f, ensure_ascii=False)
    except Exception:
        logger.exception('Failed to save user_lang')

def load_user_lang():
    global _user_lang
    if os.path.exists(USER_LANG_FILE):
        try:
            with open(USER_LANG_FILE, 'r', encoding='utf-8') as f:
                _user_lang = json.load(f)
        except Exception:
            logger.exception('Failed to load user_lang')
            _user_lang = {}

def log_action(line):
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(ACTION_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f'[{ts}] {line}\n')
    except Exception:
        logger.exception('Failed to write action log')

def log_error(line):
    ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f'[{ts}] {line}\n')
    except Exception:
        logger.exception('Failed to write error log')

# ---------------- CSV load/save ----------------
def github_download():
    if not GITHUB_CSV_URL:
        return
    try:
        logger.info('Downloading CSV from GitHub...')
        r = requests.get(GITHUB_CSV_URL, timeout=12)
        if r.status_code == 200:
            with open(CSV_PATH, 'wb') as f:
                f.write(r.content)
            logger.info('Downloaded CSV from GitHub')
    except Exception:
        logger.exception('Failed GitHub CSV download')

def load_csv():
    global _df, _csv_mtime
    with _data_lock:
        if GITHUB_CSV_URL:
            github_download()
        if not os.path.exists(CSV_PATH):
            logger.warning('CSV not found; creating empty CSV with headers')
            # create an empty DF with essential columns
            df = pd.DataFrame(columns=EDITABLE_FIELDS + IMMUTABLE_FIELDS + ['Timestamp', 'Course', 'Address', 'AdmissionNumber', 'Name'])
            df.to_csv(CSV_PATH, index=False)
            _df = df
            _csv_mtime = os.path.getmtime(CSV_PATH)
            return
        mtime = os.path.getmtime(CSV_PATH)
        if _csv_mtime is None or mtime != _csv_mtime:
            try:
                df = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)
            except Exception:
                logger.exception('Failed reading CSV; using empty DF')
                df = pd.DataFrame()
            for c in EDITABLE_FIELDS + IMMUTABLE_FIELDS:
                if c not in df.columns:
                    df[c] = ''
            _df = df
            _csv_mtime = mtime
            logger.info('CSV loaded: %d rows', _df.shape[0])

def backup_csv(reason='manual'):
    try:
        ts = datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S')
        dest = os.path.join(BACKUP_DIR, f'data_{ts}.csv')
        if os.path.exists(CSV_PATH):
            shutil.copy2(CSV_PATH, dest)
            log_action(f'Backup created ({reason}): {dest}')
            return dest
        return None
    except Exception:
        logger.exception('Backup failed')
        return None

def save_csv_with_backup(reason='edit'):
    global _df
    with _data_lock:
        try:
            if os.path.exists(CSV_PATH):
                backup_csv(reason)
            tmp = CSV_PATH + '.tmp'
            _df.to_csv(tmp, index=False)
            os.replace(tmp, CSV_PATH)
            log_action(f'CSV saved due to {reason}')
            return True
        except Exception:
            logger.exception('Failed to save CSV')
            log_error('Failed to save CSV')
            return False

# ---------------- Data lookup ----------------
def find_user_row(email: str, phone: str):
    with _data_lock:
        if _df is None or _df.empty:
            return None, None
        mask = (_df['Email'].astype(str).str.strip().str.lower() == str(email).strip().lower()) & (_df['Phone'].astype(str).str.strip() == str(phone).strip())
        matches = _df[mask]
        if matches.empty:
            return None, None
        idx = matches.index[0]
        return idx, _df.loc[idx]

def format_user_record(row):
    order = ['FullName', 'Email', 'Phone', 'AdmissionNumber', 'Course', 'Address', 'DateOfBirth', 'BankName', 'AccountNumber', 'Timestamp']
    parts = []
    for col in order:
        if col in row.index:
            parts.append(f"*{col}*: {row[col]}")
    for col in row.index:
        if col in order:
            continue
        parts.append(f"*{col}*: {row[col]}")
    return "\n".join(parts)

# ---------------- Background threads ----------------
def csv_watcher():
    while not _stop_event.is_set():
        try:
            load_csv()
        except Exception:
            logger.exception('csv_watcher error')
        _stop_event.wait(CSV_POLL_INTERVAL)

def reminder_thread(updater: Updater):
    bot = updater.bot
    while not _stop_event.is_set():
        try:
            left = days_left_to_edit()
            # notify admins at 3 days left, 1 day left, and closure day
            if left in (3, 1):
                for aid in ADMIN_IDS:
                    try:
                        bot.send_message(chat_id=aid, text=f"⚠️ Reminder: editing window has {left} day(s) left.")
                    except Exception:
                        logger.exception('Failed to send admin reminder')
            if days_since_start() == 8:
                for aid in ADMIN_IDS:
                    try:
                        bot.send_message(chat_id=aid, text="ℹ️ Editing window has closed (day 8). Users can no longer edit their data.")
                    except Exception:
                        logger.exception('Failed to send admin closure notice')
        except Exception:
            logger.exception('reminder thread error')
        # sleep up to 24 hours but allow early wake
        for _ in range(24 * 60):
            if _stop_event.is_set():
                break
            time.sleep(60)

# ---------------- Conversation states ----------------
ASK_LANG, ASK_EMAIL, ASK_PHONE, MENU, CHOOSING_FIELD, TYPING_VALUE = range(6)

# ---------------- Decorators ----------------
def admin_only(func):
    @wraps(func)
    def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS:
            update.message.reply_text(tr(update.effective_chat.id, 'admin_only'))
            return
        return func(update, context, *args, **kwargs)
    return wrapper

# ---------------- Handlers ----------------
def start(update: Update, context: CallbackContext):
    chat_id = str(update.effective_chat.id)
    # greet and ask language preference if not set
    if chat_id not in _user_lang:
        msg = STRINGS['en']['start_lang_prompt']
        update.message.reply_text(msg)
        return ASK_LANG
    # otherwise show welcome in preferred language and ask email
    update.message.reply_text(tr(chat_id, 'welcome', support=SUPPORT_LINK), parse_mode=ParseMode.MARKDOWN)
    update.message.reply_text(tr(chat_id, 'ask_email'), parse_mode=ParseMode.MARKDOWN)
    _sessions[chat_id] = {'verified': False}
    return ASK_EMAIL

def ask_lang(update: Update, context: CallbackContext):
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip().lower()
    if text in ('en', 'english'):
        _user_lang[chat_id] = 'en'
    elif text in ('ha', 'hausa'):
        _user_lang[chat_id] = 'ha'
    else:
        # fallback: if unknown, default to english
        _user_lang[chat_id] = 'en'
    save_user_lang()
    update.message.reply_text(tr(chat_id, 'lang_set', lang=_user_lang[chat_id]))
    update.message.reply_text(tr(chat_id, 'ask_email'), parse_mode=ParseMode.MARKDOWN)
    _sessions[chat_id] = {'verified': False}
    return ASK_EMAIL

def ask_email(update: Update, context: CallbackContext):
    chat_id = str(update.effective_chat.id)
    _sessions[chat_id] = {'verified': False, 'email_try': update.message.text.strip()}
    update.message.reply_text(tr(chat_id, 'ask_phone'), parse_mode=ParseMode.MARKDOWN)
    return ASK_PHONE

def ask_phone(update: Update, context: CallbackContext):
    chat_id = str(update.effective_chat.id)
    phone = update.message.text.strip()
    email = _sessions.get(chat_id, {}).get('email_try')
    idx, row = find_user_row(email, phone)
    if idx is None:
        update.message.reply_text(tr(chat_id, 'not_found'))
        return ConversationHandler.END
    # set verified session valid for 24 hours
    verified_until = (datetime.utcnow() + timedelta(hours=24)).isoformat()
    _sessions[chat_id].update({'verified': True, 'index': int(idx), 'verified_until': verified_until})
    save_sessions()
    update.message.reply_text(tr(chat_id, 'verified'))
    return show_user_menu(update, context)

def show_user_menu(update: Update, context: CallbackContext):
    # works for both message-based and callback query contexts
    if hasattr(update, 'callback_query') and update.callback_query:
        chat_id = str(update.callback_query.message.chat.id)
        cq = update.callback_query
    else:
        chat_id = str(update.effective_chat.id)
        cq = None
    session = _sessions.get(chat_id)
    if not session or not session.get('verified'):
        if cq:
            cq.message.reply_text(tr(chat_id, 'not_verified'))
        else:
            update.message.reply_text(tr(chat_id, 'not_verified'))
        return ConversationHandler.END
    # check session expiry
    try:
        if datetime.fromisoformat(session['verified_until']) < datetime.utcnow():
            # expired
            _sessions.pop(chat_id, None)
            save_sessions()
            if cq:
                cq.message.reply_text(tr(chat_id, 'not_verified'))
            else:
                update.message.reply_text(tr(chat_id, 'not_verified'))
            return ConversationHandler.END
    except Exception:
        # if malformed, require verification again
        _sessions.pop(chat_id, None)
        save_sessions()
        if cq:
            cq.message.reply_text(tr(chat_id, 'not_verified'))
        else:
            update.message.reply_text(tr(chat_id, 'not_verified'))
        return ConversationHandler.END
    idx = session['index']
    with _data_lock:
        row = _df.loc[int(idx)]
        text = format_user_record(row)
    left = days_left_to_edit()
    allowed = 'Yes' if editing_allowed() else 'No'
    if _user_lang.get(chat_id) == 'ha':
        allowed = 'Eh' if editing_allowed() else 'A'a'
    edit_note = tr(chat_id, 'menu_edit_note', left=left, allowed=allowed)
    kb = [
        [InlineKeyboardButton(tr(chat_id, 'menu_buttons')[0], callback_data='edit')],
        [InlineKeyboardButton(tr(chat_id, 'menu_buttons')[1], callback_data='refresh'),
         InlineKeyboardButton(tr(chat_id, 'menu_buttons')[2], callback_data='logout')]
    ]
    reply_markup = InlineKeyboardMarkup(kb)
    if cq:
        cq.message.reply_text(text + '\n\n' + edit_note, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        update.message.reply_text(text + '\n\n' + edit_note, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    return MENU

def menu_button_handler(update: Update, context
