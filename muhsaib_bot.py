#!/usr/bin/env python3
# muhsaib_bot.py - Muhsaib Bot (PTB v20+ compatible)
# Features: verification, 7-day edit window, immutable fields, CSV watcher, admin broadcast, logging, backups

import os
import shutil
import json
import logging
import asyncio
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv
import pandas as pd

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Load env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@werleedattah")
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+2349039475752")
ADMIN_IDS_ENV = os.getenv("ADMIN_IDS", "")
try:
    ADMIN_IDS = {int(x.strip()) for x in ADMIN_IDS_ENV.split(",") if x.strip()}
except Exception:
    ADMIN_IDS = set()
# ensure your numeric admin ID is included
ADMIN_IDS.add(7003416998)

# Config
CSV_PATH = os.getenv("CSV_PATH", "data.csv")
DATA_DIR = os.getenv("DATA_DIR", "mcf_data")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
LOG_FILE = os.path.join(DATA_DIR, "actions.log")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
USER_LANG_FILE = os.path.join(DATA_DIR, "user_lang.json")
BOT_START_FILE = os.path.join(DATA_DIR, "bot_start.json")

EDIT_WINDOW_DAYS = 7
CSV_POLL_INTERVAL = int(os.getenv("CSV_POLL_INTERVAL", "10"))

IMMUTABLE_FIELDS = {
    "Course",
    "AdmissionNo",
    "AdmissionNumber",
    "RegNumber",
    "_idx",
    "Access",
    "Paid",
    "Admitted",
    "Trade",
    "Attend",
    "Photo",
}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("muhsaib_bot")

# Globals
_df = None
_csv_mtime = None

# Helpers
def log_action(msg: str):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.utcnow().isoformat()} - {msg}\n")
    except Exception:
        logger.exception("log_action failed")


def save_json(path: str, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("save_json failed")


def load_json(path: str, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("load_json failed")
    return default


def ensure_start_date():
    if os.path.exists(BOT_START_FILE):
        try:
            with open(BOT_START_FILE, "r", encoding="utf-8") as f:
                j = json.load(f)
                return datetime.fromisoformat(j["start_date"])
        except Exception:
            pass
    sd = datetime.utcnow()
    try:
        with open(BOT_START_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_date": sd.isoformat()}, f)
    except Exception:
        logger.exception("ensure_start_date write failed")
    return sd


BOT_START_DATE = ensure_start_date()


def days_since_start():
    return (datetime.utcnow() - BOT_START_DATE).days + 1


def days_left_to_edit():
    return max(0, EDIT_WINDOW_DAYS - (days_since_start() - 1))


def editing_allowed():
    return days_since_start() <= EDIT_WINDOW_DAYS


# CSV load/save
def load_csv():
    """Load CSV into global _df. Keeps _csv_mtime to detect external changes."""
    global _df, _csv_mtime
    if not os.path.exists(CSV_PATH):
        logger.warning("data.csv not found at %s", CSV_PATH)
        _df = pd.DataFrame()
        _csv_mtime = None
        return
    try:
        m = os.path.getmtime(CSV_PATH)
        if _csv_mtime is None or m != _csv_mtime:
            df = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)
            if "Wallet" not in df.columns:
                df["Wallet"] = "0"
            _df = df
            _csv_mtime = m
            logger.info("CSV loaded (%d rows)", len(_df))
    except Exception:
        logger.exception("load_csv error")
        _df = pd.DataFrame()


def save_csv_with_backup(reason="edit"):
    global _df
    if _df is None:
        logger.error("save_csv_with_backup: _df is None")
        return False
    try:
        if os.path.exists(CSV_PATH):
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(CSV_PATH, os.path.join(BACKUP_DIR, f"data_{ts}.csv"))
        tmp = CSV_PATH + ".tmp"
        _df.to_csv(tmp, index=False)
        os.replace(tmp, CSV_PATH)
        log_action(f"save_csv: {reason}")
        return True
    except Exception:
        logger.exception("save_csv failed")
        return False


# Find user row by email+phone
def find_user_row(email, phone):
    global _df
    if _df is None or _df.empty:
        return None, None
    email = (email or "").strip().lower()
    phone = (phone or "").strip()
    if "Email" not in _df.columns or "Phone" not in _df.columns:
        return None, None
    mask = (
        _df["Email"].astype(str).str.strip().str.lower() == email
    ) & (_df["Phone"].astype(str).str.strip() == phone)
    matches = _df[mask]
    if matches.empty:
        return None, None
    idx = matches.index[0]
    return int(idx), _df.loc[idx]


def format_user_record(row):
    global _df
    if row is None or _df is None:
        return "No data"
    lines = []
    for c in _df.columns:
        val = row.get(c, "")
        # Escape backticks/newlines politely (we use simple text, not heavy markdown)
        lines.append(f"*{c}*: {val}")
    return "\n".join(lines)


# Persistent sessions & lang
_sessions = load_json(SESSIONS_FILE, {})
_user_lang = load_json(USER_LANG_FILE, {})

# Strings minimal (expandable)
STR_EN_WELCOME = "👋 Welcome to Muhsaib Student Portal. Verification lets you view and edit your record for 7 days."

# Decorators
def _get_user_id_from_update(update: Update):
    """Best-effort user id extraction for messages and callback queries."""
    try:
        if update.effective_user and getattr(update.effective_user, "id", None):
            return int(update.effective_user.id)
        if update.effective_message and update.effective_message.from_user:
            return int(update.effective_message.from_user.id)
        # fallback to chat id
        if update.effective_chat:
            return int(update.effective_chat.id)
    except Exception:
        logger.exception("failed to get user id from update")
    return None


def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **k):
        uid = _get_user_id_from_update(update)
        if uid is None or uid not in ADMIN_IDS:
            try:
                if update.effective_message:
                    await update.effective_message.reply_text("❌ You are not authorized.")
                elif update.effective_chat:
                    await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ You are not authorized.")
            except Exception:
                logger.exception("reply failed in admin_only")
            return
        return await func(update, context, *a, **k)

    return wrapper


# Conversation states
ASK_EMAIL, ASK_PHONE, MENU, CHOOSING_FIELD, TYPING_VALUE = range(5)


# Handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(STR_EN_WELCOME)
    await update.message.reply_text("Please send your EMAIL to verify:")
    return ASK_EMAIL


async def ask_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    _sessions[cid] = {"verified": False, "email_try": update.message.text.strip()}
    save_json(SESSIONS_FILE, _sessions)
    await update.message.reply_text("Now send your PHONE (include country code):")
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    phone = update.message.text.strip()
    email = _sessions.get(cid, {}).get("email_try")
    idx, row = await context.application.run_in_threadpool(find_user_row, email, phone)
    if idx is None:
        await update.message.reply_text("⚠️ Record not found. Please contact admin.")
        return ConversationHandler.END

    # mark verified (persist session)
    _sessions[cid] = {"verified": True, "index": idx, "verified_at": datetime.utcnow().isoformat()}
    save_json(SESSIONS_FILE, _sessions)

    left = days_left_to_edit()
    display_name = _df.at[idx, "FullName"] if _df is not None and "FullName" in _df.columns else ""
    await update.message.reply_text(f"✅ Verified. Welcome, {display_name}")
    await update.message.reply_text(f"Profile editing is open for {left} day(s). Hurry!")
    log_action(f"user_verified cid={cid} row={idx}")
    return await show_menu(update, context)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    session = _sessions.get(cid)
    if not session or not session.get("verified"):
        await update.message.reply_text("You must verify first using /start")
        return ConversationHandler.END

    idx = int(session["index"])
    if _df is None or idx not in _df.index:
        await update.message.reply_text("Your record is not available. Contact admin.")
        return ConversationHandler.END

    row = _df.loc[idx]
    text = format_user_record(row)
    left = days_left_to_edit()
    allowed = "Yes" if editing_allowed() else "No"

    # Build keyboard: one button per editable field (columns - immutable)
    editable = [c for c in _df.columns if c not in IMMUTABLE_FIELDS and c not in ("Wallet", "Timestamp")]
    kb = []
    for c in editable:
        kb.append([InlineKeyboardButton(f"Edit {c}", callback_data=f"fld_{c}")])
    kb.append([InlineKeyboardButton("View Record", callback_data="view_record")])
    kb.append([InlineKeyboardButton("Logout", callback_data="logout")])
    reply_markup = InlineKeyboardMarkup(kb)

    try:
        await update.message.reply_text(text + f"\n\nEditing window days left: {left} (allowed: {allowed})", reply_markup=reply_markup)
    except Exception:
        # fallback to plain text if markup fails
        await update.message.reply_text(text + f"\n\nEditing window days left: {left} (allowed: {allowed})")
    return MENU


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cid = str(query.message.chat.id)
    data = query.data or ""
    if data == "view_record":
        session = _sessions.get(cid)
        if not session:
            await query.message.reply_text("Session expired, please /start again.")
            return MENU
        idx = int(session["index"])
        if _df is None or idx not in _df.index:
            await query.message.reply_text("Record no longer available.")
            return MENU
        row = _df.loc[idx]
        await query.message.reply_text(format_user_record(row), parse_mode=ParseMode.MARKDOWN_V2)
        return MENU

    if data == "logout":
        _sessions.pop(cid, None)
        save_json(SESSIONS_FILE, _sessions)
        await query.message.reply_text("Logged out")
        return ConversationHandler.END

    if data.startswith("fld_"):
        field = data.split("fld_", 1)[1]
        if field in IMMUTABLE_FIELDS:
            await query.message.reply_text("You are not allowed to edit this field.")
            return MENU
        if not editing_allowed():
            await query.message.reply_text("Editing window closed.")
            return MENU
        # mark editing field in session
        session = _sessions.get(cid, {})
        session["editing_field"] = field
        _sessions[cid] = session
        save_json(SESSIONS_FILE, _sessions)
        await query.message.reply_text(f"Send new value for {field}:")
        return TYPING_VALUE

    return MENU


async def receive_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    session = _sessions.get(cid)
    if not session or not session.get("verified"):
        await update.message.reply_text("Not verified")
        return ConversationHandler.END

    field = session.get("editing_field")
    if not field:
        await update.message.reply_text("No field selected")
        return MENU
    if field in IMMUTABLE_FIELDS:
        await update.message.reply_text("You cannot edit this field.")
        return MENU
    if not editing_allowed():
        await update.message.reply_text("Editing window is closed.")
        return MENU

    new = update.message.text.strip()
    idx = int(session["index"])

    if _df is None or field not in _df.columns or idx not in _df.index:
        await update.message.reply_text("Field not available for editing.")
        session.pop("editing_field", None)
        save_json(SESSIONS_FILE, _sessions)
        return MENU

    old = _df.at[idx, field]
    _df.at[idx, field] = new
    ok = await context.application.run_in_threadpool(save_csv_with_backup, f"user_edit_{cid}_{field}")
    if ok:
        await update.message.reply_text(f"✅ Updated {field} from `{old}` to `{new}`. Changes saved.")
        log_action(f"edit cid={cid} row={idx} field={field} old={old} new={new}")
    else:
        await update.message.reply_text("⚠️ Save failed. Contact admin.")
    session.pop("editing_field", None)
    save_json(SESSIONS_FILE, _sessions)
    return MENU


# Admin commands
@admin_only
async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _df is None or _df.empty:
        await update.message.reply_text("CSV empty")
        return
    for i, row in _df.iterrows():
        try:
            await update.message.reply_text(json.dumps(row.to_dict(), ensure_ascii=False))
        except Exception:
            logger.exception("cmd_all send failed")


@admin_only
async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_csv()
    await update.message.reply_text("CSV reloaded")
    log_action("admin_reload")


@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    count = 0
    for cid, s in list(_sessions.items()):
        if s.get("verified"):
            try:
                await context.bot.send_message(chat_id=int(cid), text=text)
                count += 1
            except Exception:
                logger.exception("broadcast to %s failed", cid)
    await update.message.reply_text(f"Broadcast sent to {count} users")
    log_action(f"broadcast by admin count={count}")


@admin_only
async def cmd_enable_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sd = datetime.utcnow()
    try:
        with open(BOT_START_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_date": sd.isoformat()}, f)
    except Exception:
        logger.exception("enable_edit write failed")
    global BOT_START_DATE
    BOT_START_DATE = sd
    await update.message.reply_text("Edit window enabled for 7 days from now.")
    log_action("admin_enable_edit")


@admin_only
async def cmd_disable_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sd = datetime.utcnow() - timedelta(days=1000)
    try:
        with open(BOT_START_FILE, "w", encoding="utf-8") as f:
            json.dump({"start_date": sd.isoformat()}, f)
    except Exception:
        logger.exception("disable_edit write failed")
    global BOT_START_DATE
    BOT_START_DATE = sd
    await update.message.reply_text("Edit window disabled.")
    log_action("admin_disable_edit")


# CSV watcher
_last_mtime = None


async def csv_watcher(app):
    global _last_mtime
    while True:
        try:
            if os.path.exists(CSV_PATH):
                m = os.path.getmtime(CSV_PATH)
                if _last_mtime is None or m != _last_mtime:
                    _last_mtime = m
                    load_csv()
                    logger.info("csv_watcher reloaded CSV")
                    log_action("csv_watcher: reloaded CSV")
        except Exception:
            logger.exception("csv_watcher error")
        await asyncio.sleep(CSV_POLL_INTERVAL)


# Build app
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email)],
            ASK_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            MENU: [CallbackQueryHandler(menu_callback)],
            TYPING_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_value)],
        },
        fallbacks=[CommandHandler("start", cmd_start)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^fld_|^view_record|^logout"))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("enable_edit", cmd_enable_edit))
    app.add_handler(CommandHandler("disable_edit", cmd_disable_edit))

    return app


async def startup(app):
    # load CSV and start watcher
    load_csv()
    try:
        app.create_task(csv_watcher(app))
    except Exception:
        # older/newer PTB internals: create_task may still be available on the app
        try:
            asyncio.create_task(csv_watcher(app))
        except Exception:
            logger.exception("failed to start csv_watcher")
    logger.info("Bot startup complete")
    log_action("bot_started")


def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set. Exiting.")
        return
    # initial load (keep as-is)
    load_csv()
    app = build_app()

    # === FIXED STARTUP HANDLING ===
    # Some PTB/Railway combinations don't accept `on_startup` in run_polling;
    # run the async startup coroutine manually before starting the polling loop.
    try:
        # run startup coroutine with the app instance
        asyncio.run(startup(app))
        logger.info("Startup completed successfully (manual run).")
    except Exception:
        logger.exception("startup failed (manual run)")

    # Now start long-polling (no on_startup argument)
    app.run_polling()


if __name__ == "__main__":
    main()
