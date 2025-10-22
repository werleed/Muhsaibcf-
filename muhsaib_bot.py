# muhsaib_bot.py
# Final Muhsaib Charitable Foundation Bot (synchronous, python-telegram-bot v13.15 compatible)
# NOTE: set BOT_TOKEN and other env vars in Railway or your environment before running.

import os, json, time, shutil, logging, threading
from datetime import datetime, timedelta
from functools import wraps

import requests
import pandas as pd
from deep_translator import GoogleTranslator

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ParseMode, Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler, ConversationHandler, CallbackContext

# Config
BOT_TOKEN = os.getenv('BOT_TOKEN')
CSV_PATH = os.getenv('CSV_PATH', './data.csv')
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '7003416998').split(',') if x.strip().isdigit()}
ADMIN_PASS = os.getenv('ADMIN_PASS', '')
READ_ONLY = os.getenv('READ_ONLY', 'false').lower() in ('1','true','yes')
BACKUP_DIR = './backups'
LOG_DIR = './logs'
BOT_START_FILE = './bot_start.json'
SESSIONS_FILE = './sessions.json'
USER_LANG_FILE = './user_lang.json'
ACTION_LOG_FILE = os.path.join(LOG_DIR, 'actions.log')
ERROR_LOG_FILE = os.path.join(LOG_DIR, 'errors.log')

EDITABLE_FIELDS = ['FullName','DateOfBirth','BankName','AccountNumber']
IMMUTABLE_FIELDS = ['Email','Phone','AdmissionNumber']

os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('muhsaib_bot')

_data_lock = threading.RLock()
_df = None
_csv_mtime = None
_stop_event = threading.Event()
_sessions = {}
_user_lang = {}

# Strings
STRINGS = {
 'en': {'welcome':"👋 Welcome to *Muhsaib Charitable Foundation Bot!*\\nYou can view or update your registered student information here.\\nFor support contact: {support}\\n\\nUse /language to change.",'ask_email':"Please enter your *email* to begin verification:",'ask_phone':"Now send your *phone number* (include country code e.g. 234...):",'not_found':"❌ No matching record found. Try /start or contact support.",'verified':"✅ Verification successful. Verified for 24 hours.",'menu_edit_note':"🗓️ Days left to edit: {left} (editing allowed: {allowed})\\n🔔 Note: changes may take up to 7 days to reflect on the school portal.",'menu_buttons':['✏️ Edit info','🔄 Refresh','❌ Logout'],'choose_field':"Which field would you like to edit?",'send_new_value':"Please send the new value for *{field}*",'updated_success':"*{field}* updated from:\\n`{old}`\\n to:\\n`{new}`\\n\\n🔔 Note: may take up to 7 days to reflect.",'editing_disabled':"⚠️ Editing disabled. Window closed.",'not_verified':"You must /start and verify first.",'logout':"Logged out. Use /start to verify again.",'admin_only':"❌ Not authorized.",'backup_done':"Backup created: {path}",'backup_failed':"Backup failed.",'csv_reloaded':"CSV reloaded.",'rows_count':"Rows: {n}\\nDays since start: {days}\\nDays left: {left}",'find_usage':"Usage: /find <email_or_phone>",'no_results':"No results.",'start_lang_prompt':"Choose language / Zabi harshe: reply 'en' or 'ha'",'lang_set':"Language set to {lang}."},
 'ha': {'welcome':"👋 Barka da zuwa *Muhsaib Charitable Foundation Bot!*\\nZa ka iya duba ko sabunta bayanan rajistar ka.\\nTuntubi: {support}\\n\\nUse /language to change.",'ask_email':"Don Allah shigar da *email*:",'ask_phone':"Aiko da *lambar waya* (misali 234...):",'not_found':"❌ Ba a sami bayanin da ya dace ba. Gwada /start ko tuntubi admin.",'verified':"✅ An tabbatar. Awanni 24.",'menu_edit_note':"🗓️ Kwanaki suka rage: {left} (an yarda: {allowed})\\n🔔 Lura: zai iya ɗaukar har zuwa kwanaki 7 kafin bayyana.",'menu_buttons':['✏️ Gyara bayanai','🔄 Sabunta','❌ Fita'],'choose_field':"Wane filin zaka gyara?",'send_new_value':"Aiko sabon ƙima don *{field}*",'updated_success':"*{field}* an sabunta daga:\\n`{old}`\\n zuwa:\\n`{new}`\\n\\n🔔 Lura: zai ɗauki har zuwa kwanaki 7.",'editing_disabled':"⚠️ An rufe gyara.",'not_verified':"Dole ne ka /start ka tabbatar.",'logout':"An fice. Yi /start don sake shiga.",'admin_only':"❌ Ba kai bane admin.",'backup_done':"An ƙirƙiri madadin: {path}",'backup_failed':"Madadin ya kasa.",'csv_reloaded':"An sabunta CSV.",'rows_count':"Layuka: {n}\\nKwanaki tun an fara: {days}\\nKwanaki suka rage: {left}",'find_usage':"Amfani: /find <email_ko_phone>",'no_results':"Babu sakamako.",'start_lang_prompt':"Zabi harshe: 'ha' ko 'en'",'lang_set':"An saita harshe zuwa {lang}."}
}

DEFAULT_LANG='en'

def tr(chat_id, key, **kwargs):
    lang = _user_lang.get(str(chat_id), DEFAULT_LANG)
    text = STRINGS.get(lang, STRINGS[DEFAULT_LANG]).get(key, '')
    try:
        return text.format(**kwargs)
    except Exception:
        return text

# Start date
def ensure_bot_start_date():
    if os.path.exists(BOT_START_FILE):
        try:
            with open(BOT_START_FILE,'r') as f:
                j=json.load(f); return datetime.fromisoformat(j['start_date'])
        except Exception:
            pass
    sd=datetime.utcnow()
    with open(BOT_START_FILE,'w') as f:
        json.dump({'start_date':sd.isoformat()}, f)
    return sd

BOT_START_DATE = ensure_bot_start_date()

def days_since_start(): return (datetime.utcnow()-BOT_START_DATE).days+1
def days_left_to_edit(): return max(0,7-(days_since_start()-1))
def editing_allowed(): return (not READ_ONLY) and days_since_start()<=7

# persistence
def save_sessions():
    try:
        with open(SESSIONS_FILE,'w',encoding='utf-8') as f: json.dump(_sessions,f,ensure_ascii=False)
    except: logger.exception('save_sessions')
def load_sessions():
    global _sessions
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE,'r',encoding='utf-8') as f: _sessions=json.load(f)
        except: logger.exception('load_sessions'); _sessions={}
def save_user_lang():
    try:
        with open(USER_LANG_FILE,'w',encoding='utf-8') as f: json.dump(_user_lang,f,ensure_ascii=False)
    except: logger.exception('save_user_lang')
def load_user_lang():
    global _user_lang
    if os.path.exists(USER_LANG_FILE):
        try:
            with open(USER_LANG_FILE,'r',encoding='utf-8') as f: _user_lang=json.load(f)
        except: logger.exception('load_user_lang'); _user_lang={}

def log_action(msg):
    ts=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(ACTION_LOG_FILE,'a',encoding='utf-8') as f: f.write(f'[{ts}] {msg}\\n')
    except: logger.exception('log_action')
def log_error(msg):
    ts=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open(ERROR_LOG_FILE,'a',encoding='utf-8') as f: f.write(f'[{ts}] {msg}\\n')
    except: logger.exception('log_error')

# CSV ops
def github_download():
    if not GITHUB_CSV_URL: return
    try:
        r=requests.get(GITHUB_CSV_URL, timeout=12)
        if r.status_code==200:
            with open(CSV_PATH,'wb') as f: f.write(r.content)
    except: logger.exception('github_download')

def load_csv():
    global _df,_csv_mtime
    with _data_lock:
        if GITHUB_CSV_URL: github_download()
        if not os.path.exists(CSV_PATH):
            df=pd.DataFrame(columns=EDITABLE_FIELDS+IMMUTABLE_FIELDS+['Timestamp','Course','Address','AdmissionNumber','Name'])
            df.to_csv(CSV_PATH,index=False)
            _df=df; _csv_mtime=os.path.getmtime(CSV_PATH); return
        mtime=os.path.getmtime(CSV_PATH)
        if _csv_mtime is None or mtime!=_csv_mtime:
            try:
                df=pd.read_csv(CSV_PATH,dtype=str,keep_default_na=False)
            except: logger.exception('read csv'); df=pd.DataFrame()
            for c in EDITABLE_FIELDS+IMMUTABLE_FIELDS:
                if c not in df.columns: df[c]=''
            _df=df; _csv_mtime=mtime; logger.info('CSV loaded %d rows', _df.shape[0])

def backup_csv(reason='manual'):
    try:
        ts=datetime.utcnow().strftime('%Y-%m-%d_%H-%M-%S'); dest=os.path.join(BACKUP_DIR,f'data_{ts}.csv')
        if os.path.exists(CSV_PATH): shutil.copy2(CSV_PATH,dest); log_action(f'Backup {reason}: {dest}'); return dest
        return None
    except: logger.exception('backup_csv'); return None

def save_csv_with_backup(reason='edit'):
    global _df
    with _data_lock:
        try:
            if os.path.exists(CSV_PATH): backup_csv(reason)
            tmp=CSV_PATH+'.tmp'; _df.to_csv(tmp,index=False); os.replace(tmp,CSV_PATH); log_action(f'CSV saved {reason}'); return True
        except: logger.exception('save_csv'); log_error('save_csv failed'); return False

def find_user_row(email,phone):
    with _data_lock:
        if _df is None or _df.empty: return None,None
        mask=(_df['Email'].astype(str).str.strip().str.lower()==str(email).strip().lower()) & (_df['Phone'].astype(str).str.strip()==str(phone).strip())
        matches=_df[mask]
        if matches.empty: return None,None
        idx=matches.index[0]; return idx,_df.loc[idx]

def format_user_record(row):
    order=['FullName','Email','Phone','AdmissionNumber','Course','Address','DateOfBirth','BankName','AccountNumber','Timestamp']
    parts=[]
    for col in order:
        if col in row.index: parts.append(f"*{col}*: {row[col]}")
    for col in row.index:
        if col in order: continue
        parts.append(f"*{col}*: {row[col]}")
    return "\\n".join(parts)

# threads
def csv_watcher():
    while not _stop_event.is_set():
        try: load_csv()
        except: logger.exception('csv_watcher')
        _stop_event.wait(8)

def reminder_thread(updater):
    bot=updater.bot
    while not _stop_event.is_set():
        try:
            left=days_left_to_edit()
            if left in (3,1):
                for aid in ADMIN_IDS:
                    try: bot.send_message(chat_id=aid,text=f"⚠️ Reminder: editing window has {left} day(s) left.")
                    except: logger.exception('admin reminder')
            if days_since_start()==8:
                for aid in ADMIN_IDS:
                    try: bot.send_message(chat_id=aid,text="ℹ️ Editing window has closed (day 8).")
                    except: logger.exception('admin closure')
        except: logger.exception('reminder_thread')
        for _ in range(24*60):
            if _stop_event.is_set(): break
            time.sleep(60)

# conversation states
ASK_LANG,ASK_EMAIL,ASK_PHONE,MENU,CHOOSING_FIELD,TYPING_VALUE=range(6)

def admin_only(func):
    @wraps(func)
    def wrapper(update,context,*a,**k):
        uid=update.effective_user.id
        if uid not in ADMIN_IDS:
            update.message.reply_text(tr(update.effective_chat.id,'admin_only'))
            return
        return func(update,context,*a,**k)
    return wrapper

# Handlers
def start(update,context):
    chat_id=str(update.effective_chat.id)
    if chat_id not in _user_lang:
        update.message.reply_text(STRINGS['en']['start_lang_prompt'])
        return ASK_LANG
    update.message.reply_text(tr(chat_id,'welcome',support='@werleedattah or https://wa.me/2349039475752'),parse_mode=ParseMode.MARKDOWN)
    update.message.reply_text(tr(chat_id,'ask_email'),parse_mode=ParseMode.MARKDOWN)
    _sessions[chat_id]={'verified':False}
    return ASK_EMAIL

def ask_lang(update,context):
    chat_id=str(update.effective_chat.id); t=update.message.text.strip().lower()
    _user_lang[chat_id]='ha' if t in ('ha','hausa') else 'en'
    save_user_lang(); update.message.reply_text(tr(chat_id,'lang_set',lang=_user_lang[chat_id])); update.message.reply_text(tr(chat_id,'ask_email'),parse_mode=ParseMode.MARKDOWN)
    _sessions[chat_id]={'verified':False}; return ASK_EMAIL

def ask_email(update,context):
    chat_id=str(update.effective_chat.id); _sessions[chat_id]={'verified':False,'email_try':update.message.text.strip()}
    update.message.reply_text(tr(chat_id,'ask_phone'),parse_mode=ParseMode.MARKDOWN); return ASK_PHONE

def ask_phone(update,context):
    chat_id=str(update.effective_chat.id); phone=update.message.text.strip(); email=_sessions.get(chat_id,{}).get('email_try')
    idx,row=find_user_row(email,phone)
    if idx is None: update.message.reply_text(tr(chat_id,'not_found')); return ConversationHandler.END
    verified_until=(datetime.utcnow()+timedelta(hours=24)).isoformat()
    _sessions[chat_id].update({'verified':True,'index':int(idx),'verified_until':verified_until}); save_sessions()
    update.message.reply_text(tr(chat_id,'verified')); return show_user_menu(update,context)

def show_user_menu(update,context):
    if hasattr(update,'callback_query') and update.callback_query: chat_id=str(update.callback_query.message.chat.id); cq=update.callback_query
    else: chat_id=str(update.effective_chat.id); cq=None
    session=_sessions.get(chat_id)
    if not session or not session.get('verified'):
        if cq: cq.message.reply_text(tr(chat_id,'not_verified'))
        else: update.message.reply_text(tr(chat_id,'not_verified'))
        return ConversationHandler.END
    try:
        if datetime.fromisoformat(session['verified_until']) < datetime.utcnow():
            _sessions.pop(chat_id,None); save_sessions()
            if cq: cq.message.reply_text(tr(chat_id,'not_verified'))
            else: update.message.reply_text(tr(chat_id,'not_verified'))
            return ConversationHandler.END
    except Exception:
        _sessions.pop(chat_id,None); save_sessions()
        if cq: cq.message.reply_text(tr(chat_id,'not_verified'))
        else: update.message.reply_text(tr(chat_id,'not_verified'))
        return ConversationHandler.END
    idx=session['index']
    with _data_lock: row=_df.loc[int(idx)]; text=format_user_record(row)
    left=days_left_to_edit(); allowed='Yes' if editing_allowed() else 'No'
    if _user_lang.get(chat_id)=='ha': allowed='Eh' if editing_allowed() else "A'a"
    edit_note=tr(chat_id,'menu_edit_note',left=left,allowed=allowed)
    kb=[[InlineKeyboardButton(tr(chat_id,'menu_buttons')[0],callback_data='edit')],[InlineKeyboardButton(tr(chat_id,'menu_buttons')[1],callback_data='refresh'),InlineKeyboardButton(tr(chat_id,'menu_buttons')[2],callback_data='logout')]]
    reply_markup=InlineKeyboardMarkup(kb)
    if cq: cq.message.reply_text(text+'\\n\\n'+edit_note,parse_mode=ParseMode.MARKDOWN,reply_markup=reply_markup)
    else: update.message.reply_text(text+'\\n\\n'+edit_note,parse_mode=ParseMode.MARKDOWN,reply_markup=reply_markup)
    return MENU

def menu_button_handler(update,context):
    query=update.callback_query; chat_id=str(query.message.chat.id); query.answer(); data=query.data
    if data=='edit':
        if not editing_allowed(): query.message.reply_text(tr(chat_id,'editing_disabled')); return MENU
        buttons=[[InlineKeyboardButton(f,callback_data=f'fld_{f}')] for f in EDITABLE_FIELDS]; buttons.append([InlineKeyboardButton('⬅ Back',callback_data='back')])
        query.message.reply_text(tr(chat_id,'choose_field'),reply_markup=InlineKeyboardMarkup(buttons)); return CHOOSING_FIELD
    if data=='refresh':
        load_csv(); session=_sessions.get(chat_id)
        if not session or not session.get('verified'): query.message.reply_text(tr(chat_id,'not_verified')); return ConversationHandler.END
        idx=session['index']
        with _data_lock: row=_df.loc[int(idx)]; text=format_user_record(row)
        query.message.reply_text('Refreshed record:\\n'+text,parse_mode=ParseMode.MARKDOWN); return MENU
    if data=='logout':
        _sessions.pop(chat_id,None); save_sessions(); query.message.reply_text(tr(chat_id,'logout')); return ConversationHandler.END
    if data.startswith('fld_'):
        field=data.split('fld_',1)[1]
        if field not in EDITABLE_FIELDS: query.message.reply_text('This field cannot be edited.'); return MENU
        _sessions[chat_id]['editing_field']=field; save_sessions(); query.message.reply_text(tr(chat_id,'send_new_value',field=field),parse_mode=ParseMode.MARKDOWN); return TYPING_VALUE
    if data=='back': return show_user_menu(update,context)
    query.message.reply_text('Unknown action'); return MENU

def receive_new_value(update,context):
    chat_id=str(update.effective_chat.id); session=_sessions.get(chat_id)
    if not session or not session.get('verified'): update.message.reply_text(tr(chat_id,'not_verified')); return ConversationHandler.END
    field=session.get('editing_field')
    if not field: update.message.reply_text('No field selected.'); return MENU
    if not editing_allowed(): update.message.reply_text(tr(chat_id,'editing_disabled')); return MENU
    new_value=update.message.text.strip(); idx=int(session['index'])
    with _data_lock: old=_df.at[idx,field]; _df.at[idx,field]=new_value
    saved=save_csv_with_backup(reason=f'edit_by_{chat_id}_{field}')
    if saved:
        update.message.reply_text(tr(chat_id,'updated_success',field=field,old=old,new=new_value),parse_mode=ParseMode.MARKDOWN)
        log_action(f'User {chat_id} updated {field} from \"{old}\" to \"{new_value}\" row {idx}')
    else:
        update.message.reply_text('⚠️ Failed to save. Contact admin.')
    session.pop('editing_field',None); save_sessions()
    with _data_lock: row=_df.loc[idx]; text=format_user_record(row)
    update.message.reply_text('Updated record:\\n'+text,parse_mode=ParseMode.MARKDOWN); return MENU

@admin_only
def cmd_all(update,context):
    with _data_lock:
        if _df is None or _df.empty: update.message.reply_text('CSV empty'); return
        cols=['FullName','Email','Phone','AdmissionNumber']
        [update.message.reply_text(f"{i}: {row.to_dict()}") for i, row in _df[cols].iterrows()]

@admin_only
def cmd_reload(update,context):
    load_csv(); update.message.reply_text('CSV reloaded.')

@admin_only
def cmd_stats(update,context):
    with _data_lock: n=0 if _df is None else _df.shape[0]
    update.message.reply_text(f'Rows: {n}\\nDays since start: {days_since_start()}\\nDays left: {days_left_to_edit()}')

@admin_only
def cmd_backup(update,context):
    dest=backup_csv(reason='admin_command')
    if dest: update.message.reply_text(f'Backup: {dest}')
    else: update.message.reply_text('Backup failed.')

@admin_only
def cmd_find(update,context):
    args=context.args
    if not args: update.message.reply_text('Usage: /find <email_or_phone>'); return
    q=' '.join(args).strip()
    with _data_lock:
        if _df is None or _df.empty: update.message.reply_text('CSV empty'); return
        mask=_df['Email'].astype(str).str.contains(q,case=False,na=False)|_df['Phone'].astype(str).str.contains(q,na=False)
        results=_df[mask]
        if results.empty: update.message.reply_text('No results'); return
        for i,row in results.iterrows(): update.message.reply_text(format_user_record(row),parse_mode=ParseMode.MARKDOWN)

def cmd_help(update,context):
    update.message.reply_text('/start /help /language')

def cmd_language(update,context):
    update.message.reply_text(STRINGS['en']['start_lang_prompt']); return ASK_LANG

def cancel(update,context):
    update.message.reply_text('Cancelled.'); return ConversationHandler.END

def unknown(update,context):
    update.message.reply_text('Unknown command.')

def main():
    global _df,_csv_mtime
    load_sessions(); load_user_lang(); load_csv()
    if not BOT_TOKEN:
        logger.error('BOT_TOKEN not set.'); return
    updater=Updater(BOT_TOKEN,use_context=True); dp=updater.dispatcher
    conv=ConversationHandler(entry_points=[CommandHandler('start',start)],
                             states={ASK_LANG:[MessageHandler(Filters.text & ~Filters.command,ask_lang)],
                                     ASK_EMAIL:[MessageHandler(Filters.text & ~Filters.command,ask_email)],
                                     ASK_PHONE:[MessageHandler(Filters.text & ~Filters.command,ask_phone)],
                                     MENU:[CallbackQueryHandler(menu_button_handler)],
                                     CHOOSING_FIELD:[CallbackQueryHandler(menu_button_handler)],
                                     TYPING_VALUE:[MessageHandler(Filters.text & ~Filters.command,receive_new_value)]},
                             fallbacks=[CommandHandler('cancel',cancel)],allow_reentry=True)
    dp.add_handler(conv); dp.add_handler(CommandHandler('help',cmd_help)); dp.add_handler(CommandHandler('language',cmd_language))
    dp.add_handler(CommandHandler('all',cmd_all)); dp.add_handler(CommandHandler('reload',cmd_reload)); dp.add_handler(CommandHandler('stats',cmd_stats))
    dp.add_handler(CommandHandler('backup',cmd_backup)); dp.add_handler(CommandHandler('find',cmd_find,pass_args=True))
    dp.add_handler(MessageHandler(Filters.command,unknown))
    _stop_event.clear(); t1=threading.Thread(target=csv_watcher,daemon=True); t1.start()
    t2=threading.Thread(target=reminder_thread,args=(updater,),daemon=True); t2.start()
    logger.info('Starting bot...'); updater.start_polling(); updater.idle()
    _stop_event.set(); t1.join(timeout=2); t2.join(timeout=2)

if __name__ == '__main__':
    try: main()
    except Exception as e:
        logger.exception('Fatal'); log_error(str(e))
