#!/usr/bin/env python3
# muhsaibcf_bot_final.py - Muhsaib Bot v10 (production-ready)
# Features: verification, 7-day edit window, immutable fields, CSV watcher, admin broadcast, logging, backups
import os, shutil, json, logging, asyncio, time
from datetime import datetime, timedelta
from functools import wraps
from dotenv import load_dotenv
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters

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
CSV_POLL_INTERVAL = 10

IMMUTABLE_FIELDS = {"Course","AdmissionNo","AdmissionNumber","RegNumber","AdmissionNo","_idx","Access","Paid","Admitted","Trade","Attend","Photo"}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("muhsaib_v10")

# Globals
_df = None
_csv_mtime = None

# Helpers
def log_action(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.utcnow().isoformat()} - {msg}\n")
    except Exception:
        logger.exception("log_action failed")

def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("save_json failed")

def load_json(path, default):
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
            with open(BOT_START_FILE,"r",encoding="utf-8") as f:
                j = json.load(f)
                return datetime.fromisoformat(j["start_date"])
        except Exception:
            pass
    sd = datetime.utcnow()
    with open(BOT_START_FILE,"w",encoding="utf-8") as f:
        json.dump({"start_date": sd.isoformat()}, f)
    return sd

BOT_START_DATE = ensure_start_date()
def days_since_start(): return (datetime.utcnow() - BOT_START_DATE).days + 1
def days_left_to_edit(): return max(0, EDIT_WINDOW_DAYS - (days_since_start()-1))
def editing_allowed(): return days_since_start() <= EDIT_WINDOW_DAYS

# CSV load/save
def load_csv():
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
            # ensure Wallet column exists to avoid crashes if referenced
            if "Wallet" not in df.columns:
                df["Wallet"] = "0"
            _df = df
            _csv_mtime = m
            logger.info("CSV loaded (%d rows)", len(_df))
    except Exception:
        logger.exception("load_csv error"); _df = pd.DataFrame()

def save_csv_with_backup(reason="edit"):
    global _df
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
        logger.exception("save_csv failed"); return False

# Find user row by email+phone
def find_user_row(email, phone):
    if _df is None or _df.empty: return None, None
    email = (email or "").strip().lower(); phone = (phone or "").strip()
    mask = (_df["Email"].astype(str).str.strip().str.lower() == email) & (_df["Phone"].astype(str).str.strip() == phone)
    matches = _df[mask]
    if matches.empty: return None, None
    idx = matches.index[0]; return int(idx), _df.loc[idx]

def format_user_record(row):
    if row is None: return "No data"
    lines = []
    for c in _df.columns:
        lines.append(f"*{c}*: {row.get(c,'')}")
    return "\n".join(lines)

# Persistent sessions & lang
_sessions = load_json(SESSIONS_FILE, {})
_user_lang = load_json(USER_LANG_FILE, {})

# Strings minimal (can be expanded)
STR_EN_WELCOME = "👋 Welcome to Muhsaib Student Portal. Verification lets you view and edit your record for 7 days."
STR_HI = "Hello!"

# Decorators
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *a, **k):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS:
            if update.effective_message: await update.effective_message.reply_text("❌ You are not authorized.")
            return
        return await func(update, context, *a, **k)
    return wrapper

# Conversation states
ASK_EMAIL, ASK_PHONE, MENU, CHOOSING_FIELD, TYPING_VALUE = range(5)

# Handlers
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; cid = str(chat.id)
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
    cid = str(update.effective_chat.id); phone = update.message.text.strip(); email = _sessions.get(cid,{}).get("email_try")
    idx, row = await context.application.run_in_threadpool(find_user_row, email, phone)
    if idx is None:
        await update.message.reply_text("⚠️ Record not found. Please contact admin."); return ConversationHandler.END
    # mark verified permanently (user shouldn't have to login again)
    _sessions[cid] = {"verified": True, "index": idx, "verified_at": datetime.utcnow().isoformat()}
    save_json(SESSIONS_FILE, _sessions)
    # notify user with countdown
    left = days_left_to_edit()
    await update.message.reply_text(f"✅ Verified. Welcome, {_df.at[idx,'FullName'] if 'FullName' in _df.columns else ''}")
    await update.message.reply_text(f"Profile editing is open for {left} day(s). Hurry!")
    log_action(f"user_verified cid={cid} row={idx}")
    return await show_menu(update, context)

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    session = _sessions.get(cid)
    if not session or not session.get("verified"):
        await update.message.reply_text("You must verify first using /start"); return ConversationHandler.END
    idx = int(session["index"]); row = _df.loc[idx]
    text = format_user_record(row)
    left = days_left_to_edit(); allowed = "Yes" if editing_allowed() else "No"
    kb = []
    # dynamic editable fields: columns minus immutable
    editable = [c for c in _df.columns if c not in IMMUTABLE_FIELDS and c not in ("Wallet","Timestamp")]
    for c in editable:
        kb.append([InlineKeyboardButton(f"Edit {c}", callback_data=f"fld_{c}")])
    kb.append([InlineKeyboardButton("View Record", callback_data="view_record")])
    kb.append([InlineKeyboardButton("Logout", callback_data="logout")])
    await update.message.reply_text(text + f"\n\nEditing window days left: {left} (allowed: {allowed})", reply_markup=InlineKeyboardMarkup(kb))
    return MENU

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer(); cid = str(query.message.chat.id); data = query.data
    if data == "view_record":
        session = _sessions.get(cid); idx = int(session["index"]); row = _df.loc[idx]
        await query.message.reply_text(format_user_record(row), parse_mode=ParseMode.MARKDOWN_V2); return MENU
    if data == "logout":
        _sessions.pop(cid, None); save_json(SESSIONS_FILE, _sessions); await query.message.reply_text("Logged out"); return ConversationHandler.END
    if data.startswith("fld_"):
        field = data.split("fld_",1)[1]
        if field in IMMUTABLE_FIELDS:
            await query.message.reply_text("You are not allowed to edit this field."); return MENU
        if not editing_allowed():
            await query.message.reply_text("Editing window closed."); return MENU
        _sessions[cid]["editing_field"] = field; save_json(SESSIONS_FILE, _sessions)
        await query.message.reply_text(f"Send new value for {field}:"); return TYPING_VALUE
    return MENU

async def receive_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id); session = _sessions.get(cid)
    if not session or not session.get("verified"): await update.message.reply_text("Not verified"); return ConversationHandler.END
    field = session.get("editing_field")
    if not field: await update.message.reply_text("No field selected"); return MENU
    if field in IMMUTABLE_FIELDS:
        await update.message.reply_text("You cannot edit this field."); return MENU
    if not editing_allowed():
        await update.message.reply_text("Editing window is closed."); return MENU
    new = update.message.text.strip(); idx = int(session["index"])
    # Only write if column exists
    if field not in _df.columns:
        await update.message.reply_text("Field not available for editing."); session.pop("editing_field",None); save_json(SESSIONS_FILE,_sessions); return MENU
    old = _df.at[idx, field]
    _df.at[idx, field] = new
    ok = await context.application.run_in_threadpool(save_csv_with_backup, f"user_edit_{cid}_{field}")
    if ok:
        await update.message.reply_text(f"✅ Updated {field} from `{old}` to `{new}`. Changes saved.")
        log_action(f"edit cid={cid} row={idx} field={field} old={old} new={new}")
    else:
        await update.message.reply_text("⚠️ Save failed. Contact admin.")
    session.pop("editing_field",None); save_json(SESSIONS_FILE,_sessions)
    return MENU

# Admin commands
@admin_only
async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _df is None or _df.empty: await update.message.reply_text("CSV empty"); return
    for i,row in _df.iterrows():
        await update.message.reply_text(json.dumps(row.to_dict(), ensure_ascii=False))

@admin_only
async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_csv(); await update.message.reply_text("CSV reloaded"); log_action("admin_reload")

@admin_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)
    if not text: await update.message.reply_text("Usage: /broadcast <message>"); return
    # send to all verified sessions
    count = 0
    for cid,s in list(_sessions.items()):
        if s.get("verified"):
            try:
                await context.bot.send_message(chat_id=int(cid), text=text)
                count += 1
            except Exception:
                logger.exception("broadcast to %s failed", cid)
    await update.message.reply_text(f"Broadcast sent to {count} users"); log_action(f"broadcast by admin count={count}")

@admin_only
async def cmd_enable_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # reset bot start to today
    sd = datetime.utcnow()
    with open(BOT_START_FILE,"w",encoding="utf-8") as f: json.dump({"start_date": sd.isoformat()}, f)
    global BOT_START_DATE; BOT_START_DATE = sd
    await update.message.reply_text("Edit window enabled for 7 days from now."); log_action("admin_enable_edit")

@admin_only
async def cmd_disable_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # move start date far in past to disable
    sd = datetime.utcnow() - timedelta(days=1000)
    with open(BOT_START_FILE,"w",encoding="utf-8") as f: json.dump({"start_date": sd.isoformat()}, f)
    global BOT_START_DATE; BOT_START_DATE = sd
    await update.message.reply_text("Edit window disabled."); log_action("admin_disable_edit")

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
        allow_reentry=True
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
    load_csv()
    app.create_task(csv_watcher(app))
    logger.info("Bot startup complete")
    log_action("bot_started")

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set. Exiting."); return
    load_csv()
    app = build_app()
    app.post_init(startup)
    app.run_polling()

if __name__ == "__main__": main()
