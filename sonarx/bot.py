import json, logging, secrets, re, time, random, io
from telebot import TeleBot, types
from .config import settings
from .db import Database, UNSET
from .security import SecretBox
from .pasarguard import PasarGuardClient, MarzbanClient, MarzneshinClient, PasarGuardError
from .guards import (
    ROLE_PERMISSIONS, SECTION_PERMISSIONS, maintenance_enabled_for_user, is_blocked_user,
    role_allows, admin_permission_for_callback, admin_permission_for_state
)

settings.validate()
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
log=logging.getLogger('sonarx')
bot=TeleBot(settings.bot_token,parse_mode='HTML',threaded=True,num_threads=8)
db=Database(settings.database_url or settings.database_path)
box=SecretBox(settings.fernet_key) if settings.fernet_key else None
states={}
keyboard_modes={}  # Telegram Reply and Inline keyboards are mutually exclusive per chat.
inline_message_ids={}  # All tracked Inline action messages per chat; cleared before Reply mode.

def admin_role(uid):
    if uid in settings.admin_ids:
        return 'owner'
    try:
        return db.get_admin_role(uid)
    except Exception:
        return None


def admin(uid):
    return admin_role(uid) in ROLE_PERMISSIONS


def can_admin(uid, permission):
    return role_allows(admin_role(uid), permission)


def maintenance(uid):
    """Return True only when maintenance is enabled for a non-admin user."""
    return maintenance_enabled_for_user(uid, feature, admin)


def blocked(uid):
    """Admins bypass user blocks; blocked accounts and failed lookups are denied."""
    try:
        if admin(uid):
            return False
        return is_blocked_user(db.get_user(uid), False)
    except Exception:
        log.exception('Could not evaluate blocked-user guard for uid=%s', uid)
        return is_blocked_user(None, False, lookup_failed=True)


# These guards are deliberately registered before feature handlers. The bot is a
# private-chat product: never render account, wallet, ticket or subscription data
# into a group/channel. Blocked users are stopped before any stateful handler runs.
GUARDED_CONTENT_TYPES = [
    'text', 'audio', 'document', 'photo', 'sticker', 'video', 'video_note',
    'voice', 'contact', 'location', 'venue', 'animation', 'dice', 'web_app_data'
]


@bot.message_handler(func=lambda m: getattr(m.chat, 'type', None) != 'private',
                     content_types=GUARDED_CONTENT_TYPES)
def reject_non_private_message(m):
    if getattr(m, 'from_user', None):
        try:
            bot.send_message(m.from_user.id, 'برای حفظ حریم خصوصی، SonarX فقط در گفتگوی خصوصی قابل استفاده است.')
        except Exception:
            pass


@bot.callback_query_handler(func=lambda c: not getattr(c, 'message', None)
                            or getattr(c.message.chat, 'type', None) != 'private')
def reject_non_private_callback(c):
    try:
        bot.answer_callback_query(c.id, 'این عملیات فقط در گفتگوی خصوصی ربات قابل انجام است.', show_alert=True)
    except Exception:
        pass


@bot.message_handler(func=lambda m: getattr(m.chat, 'type', None) == 'private'
                     and getattr(m, 'from_user', None) is not None
                     and blocked(m.from_user.id),
                     content_types=GUARDED_CONTENT_TYPES)
def reject_blocked_message(m):
    bot.send_message(m.chat.id, 'دسترسی این حساب به ربات مسدود شده است. برای پیگیری با پشتیبانی تماس بگیرید.')


@bot.callback_query_handler(func=lambda c: blocked(c.from_user.id))
def reject_blocked_callback(c):
    bot.answer_callback_query(c.id, 'دسترسی این حساب به ربات مسدود شده است.', show_alert=True)


def insufficient_admin_callback(c):
    permission = admin_permission_for_callback(c.data)
    return permission is not None and not can_admin(c.from_user.id, permission)


@bot.callback_query_handler(func=insufficient_admin_callback)
def reject_admin_permission(c):
    bot.answer_callback_query(c.id, 'نقش مدیریتی شما اجازه انجام این عملیات را ندارد.', show_alert=True)


def user_access_gate_callback(c):
    # Membership, phone verification and maintenance apply to every customer
    # callback, not only purchase-related screens. Keep the membership re-check
    # action available so a user can pass the gate, unless maintenance is active.
    if admin(c.from_user.id):
        return False
    if c.data == 'membership_check':
        if maintenance(c.from_user.id):
            try: bot.answer_callback_query(c.id, 'ربات در حال تعمیرات است.', show_alert=True)
            except Exception: pass
            return True
        return False
    return not access_gate(c.from_user.id, c.message.chat.id, c)


@bot.callback_query_handler(func=user_access_gate_callback)
def reject_user_access_callback(c):
    # access_gate() already displayed the required prompt or alert.
    return None


def insufficient_admin_state_message(m):
    if not getattr(m, 'from_user', None):
        return False
    step = states.get(m.from_user.id, {}).get('step')
    permission = admin_permission_for_state(step)
    return permission is not None and not can_admin(m.from_user.id, permission)


@bot.message_handler(func=insufficient_admin_state_message, content_types=['text'])
def reject_admin_state_message(m):
    states.pop(m.from_user.id, None)
    bot.send_message(m.chat.id, 'مجوز نقش مدیریتی شما برای این عملیات کافی نیست؛ مرحله جاری لغو شد.')


def safe(v):return str(v).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
def fmt(v):return f'{int(v):,} {settings.currency_label}'
def product_is_unlimited(p):return int(p.get('traffic_gb') or 0)==0 or int(p.get('days') or 0)==0
def feature(key,default='1'):return db.get_setting('feature.'+key,default)=='1'
def text_setting(key,default):return db.get_setting('text.'+key,default)
def server_client(row):
    if not box:raise RuntimeError('FERNET_KEY تنظیم نشده است.')
    cls={'pasarguard':PasarGuardClient,'marzban':MarzbanClient,'marzneshin':MarzneshinClient}.get(row.get('panel_type','pasarguard'))
    if not cls:raise RuntimeError('نوع پنل پشتیبانی نمی‌شود: '+str(row.get('panel_type')))
    return cls(row['url'],row['username'],box.decrypt(row['password_enc']),settings.verify_tls)


def membership_status(uid):
    if not feature('channel_membership','0'):
        return True, [], False
    required=[x for x in db.channels() if x.get('kind')=='required']
    if not required:
        log.warning('channel_membership is enabled but no required channel is configured')
        return True, [], False
    missing=[];verification_error=False
    for item in required:
        try:
            member=bot.get_chat_member(item['chat_id'],uid)
            status=getattr(member,'status',None)
            is_member=status in {'creator','administrator','member'} or (status=='restricted' and bool(getattr(member,'is_member',False)))
            if not is_member:missing.append(item)
        except Exception:
            missing.append(item);verification_error=True
            log.warning('Required channel membership could not be checked: %s',item.get('chat_id'),exc_info=True)
    return not missing,missing,verification_error

def membership_prompt(chat_id,uid,missing,message_id=None,verification_error=False):
    kb=types.InlineKeyboardMarkup(row_width=1)
    channels=db.channels()
    join_url=next((x['chat_id'] for x in channels if x.get('kind')=='required_url'),None)
    for item in missing:
        target=item.get('chat_id','')
        url=join_url or (f"https://t.me/{target[1:]}" if target.startswith('@') else None)
        if url and url.startswith('https://'):
            kb.add(types.InlineKeyboardButton('عضویت در '+target[:32],url=url))
    kb.add(types.InlineKeyboardButton('🔄 بررسی عضویت',callback_data='membership_check'))
    text='<b>🔒 دسترسی محدود است</b>\nبرای استفاده از خدمات، ابتدا در کانال(های) الزامی عضو شوید و سپس «بررسی عضویت» را بزنید.'
    if verification_error:
        text+='\n\n<i>بررسی عضویت از سمت تلگرام ناموفق بود؛ مطمئن شوید ربات در کانال دسترسی لازم دارد.</i>'
    return edit_or_send(chat_id,text,kb,message_id)

def request_phone(chat_id,uid,message_id=None):
    if message_id:
        try:bot.edit_message_reply_markup(chat_id,message_id,reply_markup=None)
        except Exception:pass
    states[uid]={'step':'phone_verify','_return_to':'user_home'}
    kb=types.ReplyKeyboardMarkup(resize_keyboard=True,one_time_keyboard=True,row_width=1)
    kb.row(types.KeyboardButton('📱 اشتراک‌گذاری شماره تلفن',request_contact=True))
    kb.row(types.KeyboardButton(REPLY_CANCEL))
    bot.send_message(chat_id,'<b>📱 تأیید شماره تلفن</b>\nبرای ادامه، شماره خود را با دکمه زیر به‌صورت مستقیم با ربات به اشتراک بگذارید.',reply_markup=kb)
    keyboard_modes[uid]='reply'

def access_gate(uid,chat_id,c=None):
    if int(chat_id) <= 0:
        if c:
            try: bot.answer_callback_query(c.id, 'برای حفظ حریم خصوصی، این عملیات فقط در گفتگوی خصوصی انجام می‌شود.', show_alert=True)
            except Exception: pass
        else:
            try: bot.send_message(chat_id, 'برای استفاده از SonarX، لطفاً وارد گفتگوی خصوصی ربات شوید.')
            except Exception: pass
        return False
    if not admin(uid) and maintenance(uid):
        if c:
            try: bot.answer_callback_query(c.id, 'ربات در حال تعمیرات است.', show_alert=True)
            except Exception: pass
        else:
            bot.send_message(chat_id, 'ربات در حال تعمیرات است؛ پس از پایان عملیات نگهداری دوباره تلاش کنید.')
        return False
    ok,missing,err=membership_status(uid)
    if not ok:
        if c:
            try:bot.answer_callback_query(c.id)
            except Exception:pass
        membership_prompt(chat_id,uid,missing,c.message.message_id if c else None,err)
        return False
    if feature('phone_verify','0'):
        user=db.get_user(uid) or {}
        if not user.get('phone_verified'):
            if c:
                clear_inline(c)
                try:bot.answer_callback_query(c.id)
                except Exception:pass
                request_phone(chat_id,uid)
            else:
                clear_chat_inline(chat_id)
                request_phone(chat_id,uid)
            return False
    return True


def user_access_gate_message(m):
    if not getattr(m, 'from_user', None) or getattr(m.chat, 'type', None) != 'private':
        return False
    uid = m.from_user.id
    if admin(uid):
        return False
    command = (m.text or '').split(maxsplit=1)[0].split('@', 1)[0] if m.text else ''
    if command == '/start':
        return False  # /start creates the user row before evaluating the gate.
    if not db.get_user(uid):
        return False  # Let the /start handler establish a new account first.
    state = states.get(uid, {})
    if state.get('step') == 'phone_verify':
        if maintenance(uid):
            return not access_gate(uid, m.chat.id)
        if getattr(m, 'content_type', None) == 'contact':
            return False
        # Let ordinary text reach the phone-verification hint. Do not let
        # commands or Reply-keyboard controls cancel/escape the verification.
        if m.text and not m.text.startswith('/') and m.text not in {
            REPLY_BACK, REPLY_CANCEL, REPLY_HOME, REPLY_CONFIRM_PURCHASE,
            REPLY_BACK_TO_PLAN, REPLY_CLOSE_TICKET, REPLY_CONFIRM_ADDON,
            REPLY_CONFIRM_REFUND, REPLY_APPROVE_DEPOSIT, REPLY_REJECT_DEPOSIT,
            REPLY_CONFIRM_DEPOSIT, REPLY_CONFIRM_DELETE_LINK
        }:
            return False
    return not access_gate(uid, m.chat.id)


@bot.message_handler(func=user_access_gate_message, content_types=GUARDED_CONTENT_TYPES)
def reject_user_access_message(m):
    # user_access_gate_message() already displayed the required prompt.
    return None


# ---------------- UX layer: mutually exclusive Inline / Reply keyboard modes ----------------
REPLY_BACK = '↩️ بازگشت'
REPLY_CANCEL = '✖️ لغو عملیات'
REPLY_HOME = '🏠 منوی اصلی'
REPLY_CONFIRM_PURCHASE = '✅ تأیید نهایی خرید'
REPLY_BACK_TO_PLAN = '↩️ بازگشت به پلن'
REPLY_CLOSE_TICKET = '🔒 بستن تیکت'
REPLY_CONFIRM_ADDON = '✅ تأیید تغییر سرویس'
REPLY_CONFIRM_REFUND = '💸 تأیید بازپرداخت'
REPLY_APPROVE_DEPOSIT = '✅ تأیید شارژ'
REPLY_REJECT_DEPOSIT = '❌ رد رسید'
REPLY_CONFIRM_DEPOSIT = 'ثبت نهایی نتیجه رسید'
REPLY_CONFIRM_DELETE_LINK = '🗑 تأیید حذف لینک'


def reply_keyboard(*rows):
    """Build a Telegram ReplyKeyboard. Use only while no Inline keyboard is visible."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False, row_width=2)
    for row in rows:
        kb.row(*(types.KeyboardButton(x) for x in row))
    return kb


def remove_reply_keyboard(chat_id, text=''):
    """Switch back to Inline mode. ReplyKeyboardRemove is sent before the next screen."""
    try:
        bot.send_message(chat_id, text or ' ', reply_markup=types.ReplyKeyboardRemove())
        keyboard_modes[chat_id] = 'inline'
    except Exception:
        log.debug('Reply keyboard removal failed', exc_info=True)


def ensure_inline_mode(chat_id, uid=None):
    """Clear legacy/persistent Reply keyboards once before rendering an Inline screen."""
    key = uid if uid is not None else chat_id
    if keyboard_modes.get(key) != 'inline':
        try:
            bot.send_message(chat_id, 'کیبورد قدیمی پاک شد؛ از منوی جدید استفاده کنید.', reply_markup=types.ReplyKeyboardRemove())
        except Exception:
            log.debug('Could not clear previous Reply keyboard', exc_info=True)
        keyboard_modes[key] = 'inline'


def register_inline_message(chat_id, message_id):
    if message_id:
        inline_message_ids.setdefault(chat_id, set()).add(message_id)


def clear_chat_inline(chat_id):
    for message_id in inline_message_ids.pop(chat_id, set()):
        try:
            bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        except Exception:
            pass


def clear_other_inline(chat_id, keep_message_id=None):
    tracked = inline_message_ids.get(chat_id, set())
    for message_id in list(tracked):
        if message_id == keep_message_id:
            continue
        try:
            bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        except Exception:
            pass
        tracked.discard(message_id)


def clear_inline(c):
    chat_id = c.message.chat.id
    register_inline_message(chat_id, c.message.message_id)
    clear_chat_inline(chat_id)


def enter_reply_mode(c, text, state, rows=None, return_to='user_home'):
    """Remove the current Inline keyboard, then show a purpose-built Reply keyboard."""
    clear_inline(c)
    state['_return_to'] = return_to
    states[c.from_user.id] = state
    if rows is None:
        rows = [(REPLY_BACK, REPLY_CANCEL)]
    bot.send_message(c.message.chat.id, text, reply_markup=reply_keyboard(*rows))
    keyboard_modes[c.from_user.id] = 'reply'
    try:
        bot.answer_callback_query(c.id)
    except Exception:
        pass


def prompt_reply_mode(chat_id, uid, text, state, rows=None, return_to='user_home'):
    state['_return_to'] = return_to
    states[uid] = state
    if rows is None:
        rows = [(REPLY_BACK, REPLY_CANCEL)]
    bot.send_message(chat_id, text, reply_markup=reply_keyboard(*rows))
    keyboard_modes[uid] = 'reply'


def finish_reply_mode(chat_id, uid, text, return_to='user_home'):
    states.pop(uid, None)
    remove_reply_keyboard(chat_id, text)
    if return_to.startswith('admin_section:') and admin(uid):
        return admin_section_show(chat_id, return_to.split(':', 1)[1], uid=uid)
    return edit_or_send(chat_id, home_text(uid), home(uid))


def edit_or_send(chat_id, text, markup=None, message_id=None):
    ensure_inline_mode(chat_id)
    clear_other_inline(chat_id, message_id)
    if message_id:
        try:
            result = bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, disable_web_page_preview=True)
            if markup is not None:
                register_inline_message(chat_id, message_id)
            else:
                inline_message_ids.get(chat_id, set()).discard(message_id)
            return result
        except Exception:
            pass
    result = bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)
    if markup is not None:
        register_inline_message(chat_id, getattr(result, 'message_id', None))
    return result


def home_text(uid):
    u = db.get_user(uid) or {}
    balance = fmt(u.get('balance', 0))
    return (
        '<b>SONARX  /  پنل کاربری</b>\n'
        '<i>مدیریت سرویس، پرداخت و پشتیبانی در یک جا</i>\n\n'
        f'👤 شناسه: <code>{uid}</code>\n'
        f'💳 اعتبار کیف پول: <b>{balance}</b>\n\n'
        'برای ادامه، یکی از بخش‌های زیر را انتخاب کنید.'
    )


def home(uid):
    """Compact customer dashboard. Inline is the default navigation mode."""
    buttons = []
    def label(key, default):
        return (db.get_setting('button.' + key + '.label', default) or default)[:42]
    def enabled(key):
        return db.get_setting('button.' + key + '.enabled', '1') == '1'
    options = [
        ('shop', '🛍 خرید سرویس', 'shop'), ('orders', '📦 سرویس‌های من', 'orders'),
        ('profile', '👤 حساب کاربری', 'profile'), ('deposit', '💳 افزایش موجودی', 'deposit'),
        ('support', '🎫 پشتیبانی', 'support'), ('gift', '🎁 کد هدیه', 'redeem_gift'),
    ]
    required_features = {'shop':'shop_enabled', 'deposit':'deposits_enabled', 'support':'support_enabled', 'gift':'gift_codes'}
    for key, default, callback in options:
        if enabled(key) and (key not in required_features or feature(required_features[key], '1')):
            buttons.append(types.InlineKeyboardButton(label(key, default), callback_data=callback))
    if feature('test_account','0'):
        buttons.append(types.InlineKeyboardButton('🆓 اکانت تست',callback_data='trial'))
    if feature('custom_plan','0'):
        buttons.append(types.InlineKeyboardButton('🧩 پلن دلخواه',callback_data='custom_plan'))
    if feature('reseller','0'):
        if db.get_reseller(uid):
            if feature('reseller_panel','0'):
                buttons.append(types.InlineKeyboardButton('👨‍💼 پنل نمایندگی',callback_data='reseller_panel'))
        else:
            buttons.append(types.InlineKeyboardButton('🤝 درخواست نمایندگی',callback_data='reseller_request'))
    if feature('referral', '0'):
        buttons.append(types.InlineKeyboardButton('🤝 دعوت دوستان', callback_data='referral'))
    buttons.append(types.InlineKeyboardButton('🎫 تیکت‌های من', callback_data='my_tickets'))
    if feature('dice_game', '0'):
        buttons.append(types.InlineKeyboardButton('🎲 شانس و جایزه', callback_data='dice_game'))
    if feature('lottery', '0'):
        buttons.append(types.InlineKeyboardButton('🎟 شرکت در قرعه‌کشی', callback_data='lottery_join'))
    buttons.append(types.InlineKeyboardButton('📚 آموزش و راهنما', callback_data='help_center'))
    try:
        custom = [x for x in json.loads(db.get_setting('custom_buttons', '[]'))[:6]
                  if x.get('label') and x.get('url', '').startswith('https://')]
        buttons.extend(types.InlineKeyboardButton(item['label'][:45], url=item['url']) for item in custom)
    except Exception:
        pass
    if admin(uid):
        buttons.append(types.InlineKeyboardButton('⚙️ مرکز مدیریت', callback_data='admin'))
    k = types.InlineKeyboardMarkup(row_width=2)
    for i in range(0, len(buttons), 2):
        k.row(*buttons[i:i+2])
    return k


ADMIN_SECTIONS = {
    'overview': ('📊 نمای کلی', 'آمار، گزارش عملکرد و ردپای فعالیت‌ها', [
        ('📊 داشبورد', 'stats'), ('📈 گزارش‌ها و رتبه‌بندی', 'reports'), ('🧾 گزارش رویدادها', 'audit')]),
    'store': ('🛍 فروشگاه و سرویس‌ها', 'پلن‌ها، دسته‌ها و زیرساخت فروش', [
        ('📦 مدیریت پلن‌ها', 'admin_products'), ('🗂 دسته‌بندی‌ها', 'categories'),
        ('🖥 سرورها و پنل‌ها', 'admin_servers'), ('➕ ساخت پلن', 'add_product'),
        ('➕ افزودن سرور', 'add_server'),
        ('🆓 تنظیم اکانت تست', 'trial_settings'), ('🧩 تنظیم پلن دلخواه', 'custom_plan_settings')]),
    'finance': ('💳 مالی و پرداخت', 'کیف پول، رسیدها، تخفیف و قیمت‌گذاری', [
        ('💰 خلاصه مالی', 'finance'), ('🧾 رسیدهای در انتظار', 'admin_deposits'),
        ('🎟 کدهای تخفیف', 'discounts'), ('🎁 کدهای هدیه', 'gift_codes'),
        ('⚖️ قیمت تمدید و حجم', 'addon_prices'), ('🎁 تنظیم پاداش و کمیسیون', 'cashback_settings')]),
    'users': ('👥 کاربران و دسترسی‌ها', 'جستجو و مدیریت حساب‌ها و مدیران', [
        ('🔎 جستجوی کاربر', 'user_search'), ('👥 فهرست کاربران', 'admin_users'),
        ('💸 شارژ کیف پول کاربر', 'credit'), ('⛔ مسدودسازی کاربر', 'user_block'),
        ('📢 پیام همگانی', 'broadcast'), ('🛡 مدیران ربات', 'admin_admins')]),
    'support': ('🎫 پشتیبانی', 'رسیدگی به درخواست‌ها و ارتباط با کاربران', [
        ('📨 تیکت‌های باز', 'admin_tickets'), ('📣 کانال‌ها و گروه‌ها', 'channels'),
        ('✍️ ارسال پست کانال', 'channel_post')]),
    'growth': ('🚀 رشد و بازاریابی', 'دعوت، نمایندگی و فعالیت‌های تعاملی', [
        ('🤝 مدیریت نمایندگان', 'resellers'), ('📨 درخواست‌های نمایندگی', 'reseller_requests'),
        ('🎲 مدیریت قرعه‌کشی', 'lottery_admin'), ('🎰 تنظیم چرخ شانس', 'wheel_settings'),
        ('📣 مدیریت کانال‌ها', 'channels'), ('🎁 کدهای هدیه', 'gift_codes')]),
    'interface': ('🎨 ظاهر و تجربه کاربری', 'متن‌ها، دکمه‌ها و قابلیت‌های قابل نمایش', [
        ('🔘 مدیریت دکمه‌ها', 'button_manager'), ('🧩 تنظیمات عمومی', 'admin_config'),
        ('🎚 ماتریس قابلیت‌ها', 'feature_matrix:0')]),
    'system': ('🛠 سیستم و امنیت', 'سلامت سرویس، سفارش‌های خاص و کنترل مدیریتی', [
        ('⚠️ سفارش‌های نیازمند بررسی', 'admin_reviews'), ('🧾 لاگ مدیریتی', 'audit'),
        ('🧩 تنظیمات جامع', 'admin_config'), ('🎚 ماتریس قابلیت‌ها', 'feature_matrix:4')]),
}


def admin_section_access(uid, section):
    if uid is None:
        return True
    if section == 'system':
        return any(can_admin(uid, permission) for permission in ('orders_review', 'settings', 'security'))
    permission = SECTION_PERMISSIONS.get(section)
    return bool(permission and can_admin(uid, permission))


def admin_panel(chat_id, message_id=None, uid=None):
    if int(chat_id) <= 0:
        return None
    k = types.InlineKeyboardMarkup(row_width=2)
    try:
        live = db.stats()
        pending = len(db.pending_deposits())
        reviews = len(db.review_orders())
    except Exception:
        live, pending, reviews = {}, 0, 0
    buttons = []
    for key, (title, _desc, _items) in ADMIN_SECTIONS.items():
        if not admin_section_access(uid, key):
            continue
        if key == 'finance' and pending:
            title = f'💳 مالی و پرداخت · {pending}'
        elif key == 'support' and live.get('open_tickets'):
            title = f'🎫 پشتیبانی · {live["open_tickets"]}'
        elif key == 'system' and reviews:
            title = f'🛠 سیستم · {reviews}'
        buttons.append(types.InlineKeyboardButton(title, callback_data='admin_section:' + key))
    for i in range(0, len(buttons), 2):
        k.row(*buttons[i:i+2])
    k.add(types.InlineKeyboardButton('↩️ بازگشت به پنل کاربری', callback_data='user_home'))
    text = (
        '<b>SONARX  /  مرکز مدیریت</b>\n'
        '<i>کنترل متمرکز فروش، کاربران، مالی و زیرساخت</i>\n\n'
        f'👥 کاربران: <b>{live.get("users", 0):,}</b>  ·  🧾 رسید معلق: <b>{pending}</b>  ·  🎫 تیکت باز: <b>{live.get("open_tickets", 0)}</b>\n\n'
        'یک حوزه را انتخاب کنید تا ابزارهای همان بخش نمایش داده شوند.'
    )
    return edit_or_send(chat_id, text, k, message_id)


def admin_section_show(chat_id, section, message_id=None, uid=None):
    if int(chat_id) <= 0:
        return None
    if section not in ADMIN_SECTIONS:
        return admin_panel(chat_id, message_id, uid=uid)
    if not admin_section_access(uid, section):
        return edit_or_send(chat_id, 'نقش مدیریتی شما به این بخش دسترسی ندارد.', None, message_id)
    title, desc, items = ADMIN_SECTIONS[section]
    k = types.InlineKeyboardMarkup(row_width=2)
    visible_items = []
    for label, callback in items:
        permission = admin_permission_for_callback(callback)
        if uid is None or (permission is not None and can_admin(uid, permission)):
            visible_items.append((label, callback))
    buttons = [types.InlineKeyboardButton(label, callback_data=callback) for label, callback in visible_items]
    for i in range(0, len(buttons), 2):
        k.row(*buttons[i:i+2])
    k.add(types.InlineKeyboardButton('⬅️ بازگشت به مرکز مدیریت', callback_data='admin'))
    text = f'<b>{title}</b>\n<i>{desc}</i>\n\nابزار موردنظر را انتخاب کنید.'
    return edit_or_send(chat_id, text, k, message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('admin_section:'))
def admin_section(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    section = c.data.split(':', 1)[1]
    admin_section_show(c.message.chat.id, section, c.message.message_id, uid=c.from_user.id)
    bot.answer_callback_query(c.id)


@bot.message_handler(func=lambda m: m.text in {REPLY_BACK, REPLY_CANCEL, REPLY_HOME, REPLY_CONFIRM_PURCHASE, REPLY_BACK_TO_PLAN, REPLY_CLOSE_TICKET, REPLY_CONFIRM_ADDON, REPLY_CONFIRM_REFUND, REPLY_APPROVE_DEPOSIT, REPLY_REJECT_DEPOSIT, REPLY_CONFIRM_DEPOSIT, REPLY_CONFIRM_DELETE_LINK})
def special_reply_router(m):
    uid = m.from_user.id
    state = states.get(uid, {})
    step = state.get('step')
    action = m.text
    if action == REPLY_CONFIRM_PURCHASE and step == 'confirm_purchase':
        if not access_gate(uid,m.chat.id):
            if states.get(uid,{}).get('step')=='confirm_purchase':states.pop(uid,None)
            return
        st = states.pop(uid, {})
        remove_reply_keyboard(m.chat.id, 'در حال ثبت سفارش…')
        return process_purchase(m.chat.id, uid, st['product_id'], st.get('discount_code'))
    if action == REPLY_CONFIRM_ADDON and step == 'confirm_service_addon':
        st = states.pop(uid, {})
        remove_reply_keyboard(m.chat.id, 'در حال اعمال تغییر سرویس…')
        return process_service_addon(m.chat.id, uid, st)
    if action == REPLY_CONFIRM_REFUND and step == 'confirm_review_refund':
        st = states.pop(uid, {})
        remove_reply_keyboard(m.chat.id, 'در حال ثبت بازپرداخت…')
        return process_review_refund(m.chat.id, uid, st['order_id'])
    if action in {REPLY_APPROVE_DEPOSIT, REPLY_REJECT_DEPOSIT} and step == 'review_deposit':
        decision = 'approve' if action == REPLY_APPROVE_DEPOSIT else 'reject'
        st = dict(state)
        st.update({'step':'confirm_deposit_decision','decision':decision})
        prompt_reply_mode(
            m.chat.id, uid,
            ('<b>⚠️ تأیید نهایی شارژ</b>' if decision == 'approve' else '<b>⚠️ تأیید نهایی رد رسید</b>') + '\n'
            f"رسید: <code>#{st['deposit_id']}</code>\n"
            'این تصمیم پس از ثبت قابل برگشت خودکار نیست. برای ادامه، ثبت نهایی را انتخاب کنید.',
            st, rows=[(REPLY_CONFIRM_DEPOSIT,), (REPLY_BACK, REPLY_CANCEL)], return_to='deposit_review'
        )
        return
    if action == REPLY_CONFIRM_DEPOSIT and step == 'confirm_deposit_decision':
        st = states.pop(uid, {})
        remove_reply_keyboard(m.chat.id, 'در حال ثبت نتیجه رسید…')
        return process_deposit_decision(m.chat.id, uid, st['deposit_id'], st['decision'])
    if action == REPLY_CONFIRM_DELETE_LINK and step == 'confirm_button_delete':
        st = states.pop(uid, {})
        try:
            custom = json.loads(db.get_setting('custom_buttons', '[]'))
            index = int(st['index'])
            if index < 0 or index >= len(custom):
                return finish_reply_mode(m.chat.id, uid, 'این لینک قبلاً حذف شده است.', 'admin_section:interface')
            candidate = custom[index]
            if candidate.get('url') != st.get('url') or candidate.get('label') != st.get('label'):
                return finish_reply_mode(m.chat.id, uid, 'فهرست لینک‌ها تغییر کرده است؛ دوباره لینک موردنظر را انتخاب کنید.', 'admin_section:interface')
            removed = custom.pop(index)
            db.set_setting('custom_buttons', json.dumps(custom, ensure_ascii=False))
            db.audit(uid, 'button.custom_delete', removed.get('label', ''))
            return finish_reply_mode(m.chat.id, uid, 'لینک سفارشی حذف شد.', 'admin_section:interface')
        except Exception as exc:
            return finish_reply_mode(m.chat.id, uid, 'حذف لینک انجام نشد: ' + safe(exc), 'admin_section:interface')
    if action == REPLY_BACK_TO_PLAN and step == 'confirm_purchase':
        st = states.pop(uid, {})
        remove_reply_keyboard(m.chat.id, 'بازگشت به جزئیات پلن')
        return send_product_details(m.chat.id,st['product_id'],uid=m.from_user.id)
    if action == REPLY_CLOSE_TICKET and step == 'ticket_reply':
        tid = state.get('ticket_id')
        if tid:
            db.close_ticket(tid)
            db.audit(uid, 'ticket.close', str(tid))
        return finish_reply_mode(m.chat.id, uid, f'تیکت #{tid} بسته شد.', 'admin_section:support')
    if action in {REPLY_CANCEL, REPLY_HOME}:
        st = states.pop(uid, {})
        target = st.get('_return_to', 'user_home')
        remove_reply_keyboard(m.chat.id, 'عملیات لغو شد.' if action == REPLY_CANCEL else 'منوی اصلی')
        if action == REPLY_CANCEL and target.startswith('admin_section:') and admin(uid):
            return admin_section_show(m.chat.id, target.split(':', 1)[1], uid=m.from_user.id)
        if action == REPLY_CANCEL and target == 'deposit_review' and admin(uid):
            return admin_section_show(m.chat.id, 'finance', uid=m.from_user.id)
        if action == REPLY_CANCEL and target == 'shop' and st.get('product_id'):
            return send_product_details(m.chat.id,st['product_id'],uid=m.from_user.id)
        return edit_or_send(m.chat.id, home_text(uid), home(uid))
    if action == REPLY_BACK:
        st = states.pop(uid, {})
        target = st.get('_return_to', 'user_home')
        remove_reply_keyboard(m.chat.id, 'بازگشت به بخش قبلی')
        if target.startswith('admin_section:') and admin(uid):
            return admin_section_show(m.chat.id, target.split(':', 1)[1], uid=m.from_user.id)
        if target == 'shop' and st.get('product_id'):
            return send_product_details(m.chat.id,st['product_id'],uid=m.from_user.id)
        if target == 'service_addon' and st.get('service_id'):
            return prompt_reply_mode(m.chat.id, uid, 'مقدار جدید را به ' + ('GB' if st.get('kind') == 'gb' else 'روز') + ' وارد کنید.', {'step':'service_addon_value','kind':st['kind'],'service_id':st['service_id']}, return_to='user_home')
        if target == 'deposit_review' and st.get('deposit_id'):
            return prompt_reply_mode(m.chat.id, uid, f'<b>🧾 تعیین تکلیف رسید #{st["deposit_id"]}</b>\nتأیید شارژ یا رد رسید را انتخاب کنید.', {'step':'review_deposit','deposit_id':st['deposit_id']}, rows=[(REPLY_APPROVE_DEPOSIT, REPLY_REJECT_DEPOSIT), (REPLY_BACK, REPLY_CANCEL)], return_to='admin_section:finance')
        if target == 'admin' and admin(uid):
            return admin_panel(m.chat.id, uid=m.from_user.id)
        return edit_or_send(m.chat.id, home_text(uid), home(uid))
    # A special key was pressed outside an active workflow; restore the dashboard.
    if action == REPLY_BACK:
        return edit_or_send(m.chat.id, home_text(uid), home(uid))


@bot.message_handler(content_types=['contact'],func=lambda m:states.get(m.from_user.id,{}).get('step')=='phone_verify')
def phone_verify_contact(m):
    contact=m.contact
    if not contact or int(getattr(contact,'user_id',0) or 0)!=int(m.from_user.id):
        return bot.send_message(m.chat.id,'برای تأیید، فقط شماره خودتان را با دکمه اشتراک‌گذاری ارسال کنید.')
    phone=str(contact.phone_number or '').strip()
    digits=re.sub(r'[^0-9+]','',phone)
    if feature('iran_phone_verify','0'):
        normalized=digits.replace('0098','+98')
        if not (normalized.startswith('+98') or normalized.startswith('98')):
            return bot.send_message(m.chat.id,'این قابلیت فقط شماره‌های ایران را می‌پذیرد. شماره ایرانی خود را ارسال کنید.')
    if len(re.sub(r'\D','',digits))<8:
        return bot.send_message(m.chat.id,'شماره تلفن معتبر نیست؛ دوباره ارسال کنید.')
    db.save_phone(m.from_user.id,digits)
    db.audit(m.from_user.id,'user.phone_verified',str(m.from_user.id),'contact')
    finish_reply_mode(m.chat.id,m.from_user.id,'✅ شماره تلفن با موفقیت تأیید شد.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='phone_verify')
def phone_verify_text(m):
    bot.send_message(m.chat.id,'لطفاً از دکمه «اشتراک‌گذاری شماره تلفن» استفاده کنید؛ ارسال دستی شماره پذیرفته نمی‌شود.')

@bot.callback_query_handler(func=lambda c:c.data=='membership_check')
def membership_check(c):
    ok,missing,err=membership_status(c.from_user.id)
    if not ok:
        membership_prompt(c.message.chat.id,c.from_user.id,missing,c.message.message_id,err)
        return bot.answer_callback_query(c.id,'هنوز عضویت شما تأیید نشده است.',show_alert=True)
    if feature('phone_verify','0') and not (db.get_user(c.from_user.id) or {}).get('phone_verified'):
        clear_inline(c);request_phone(c.message.chat.id,c.from_user.id)
        return bot.answer_callback_query(c.id,'اکنون شماره تلفن را تأیید کنید.')
    if states.get(c.from_user.id,{}).get('step')=='confirm_purchase':
        states.pop(c.from_user.id,None)
    bot.answer_callback_query(c.id,'عضویت تأیید شد.')
    return edit_or_send(c.message.chat.id,home_text(c.from_user.id),home(c.from_user.id),c.message.message_id)

@bot.message_handler(commands=['start'])
def start(m):
    if getattr(m.chat, 'type', 'private') != 'private':
        return bot.reply_to(m, 'برای حفظ حریم خصوصی، SonarX را در گفتگوی خصوصی ربات اجرا کنید.')
    existing=db.get_user(m.from_user.id)
    u=db.user(m.from_user.id,m.from_user.username,m.from_user.first_name)
    if not existing and len(m.text.split())>1 and (feature('referral','0') or feature('reseller','0') or feature('reseller_commission','0')):
        try:
            inviter=int(m.text.split()[1])
            if db.get_user(inviter):
                bonus=int(db.get_setting('referral_bonus','0') or 0)
                if db.record_referral(inviter,m.from_user.id,max(0,bonus)):
                    try:bot.send_message(inviter,f'یک کاربر با لینک دعوت شما عضو شد. پاداش: {fmt(max(0,bonus))}')
                    except Exception:pass
        except Exception:log.exception('Referral attribution failed')
    if u.get('is_blocked') and not admin(m.from_user.id):return bot.send_message(m.chat.id,'حساب کاربری شما مسدود است.')
    states.pop(m.from_user.id, None)
    clear_chat_inline(m.chat.id)
    if not access_gate(m.from_user.id, m.chat.id):
        return
    welcome=text_setting('welcome','<b>به SonarX خوش آمدید</b>\nفروشگاه هوشمند سرویس‌های VPN؛ انتخاب پلن، پرداخت و تحویل خودکار.')
    bot.send_message(m.chat.id, welcome, reply_markup=types.ReplyKeyboardRemove())
    keyboard_modes[m.from_user.id] = 'inline'
    edit_or_send(m.chat.id, home_text(m.from_user.id), home(m.from_user.id))

@bot.message_handler(commands=['cancel'])
def cancel(m):
    st = states.pop(m.from_user.id, {})
    target = st.get('_return_to', 'user_home')
    remove_reply_keyboard(m.chat.id, 'عملیات لغو شد.')
    if target.startswith('admin_section:') and admin(m.from_user.id):
        return admin_section_show(m.chat.id, target.split(':', 1)[1], uid=m.from_user.id)
    if target == 'shop' and st.get('product_id'):
        return send_product_details(m.chat.id,st['product_id'],uid=m.from_user.id)
    return edit_or_send(m.chat.id, home_text(m.from_user.id), home(m.from_user.id))

def render_shop_products(chat_id,uid,category_id=None,message_id=None):
    items=db.products() if category_id is None else db.products_for_category(category_id)
    if not items:
        kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به دسته‌ها',callback_data='shop'))
        return edit_or_send(chat_id,'در این دسته فعلاً پلن فعالی وجود ندارد.',kb,message_id)
    kb=types.InlineKeyboardMarkup(row_width=1)
    lines=[]
    for index,product in enumerate(items,1):
        lines.append(f"<b>{index}. {safe(product['name'])}</b> · {fmt(product['price'])}\n{product['traffic_gb']} GB  |  {product['days'] or 'نامحدود'} روز")
        kb.add(types.InlineKeyboardButton(f"📦 {product['name'][:25]} · {fmt(product['price'])}",callback_data=f"buy:{product['id']}"))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به دسته‌ها',callback_data='shop'))
    text='<b>🛍 پلن‌های فروشگاه</b>\n\n'+'\n\n'.join(lines)
    return edit_or_send(chat_id,text,kb,message_id)

@bot.callback_query_handler(func=lambda c: c.data == 'shop')
def shop(c):
    if maintenance(c.from_user.id):
        return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    if not feature('shop_enabled'):
        return bot.answer_callback_query(c.id,'فروشگاه موقتاً غیرفعال است.',show_alert=True)
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    cats=db.categories_with_products()
    if not cats:
        render_shop_products(c.message.chat.id,c.from_user.id,None,c.message.message_id)
        bot.answer_callback_query(c.id)
        return
    kb=types.InlineKeyboardMarkup(row_width=2)
    buttons=[types.InlineKeyboardButton(f"🗂 {x['name']} · {x['product_count']}",callback_data=f"shop_category:{x['id']}") for x in cats]
    for i in range(0,len(buttons),2):kb.row(*buttons[i:i+2])
    if db.products_for_category(0):
        kb.add(types.InlineKeyboardButton('📦 سایر پلن‌ها',callback_data='shop_category:0'))
    kb.add(types.InlineKeyboardButton('🏠 منوی اصلی',callback_data='user_home'))
    edit_or_send(c.message.chat.id,'<b>🛍 فروشگاه SonarX</b>\nدسته‌بندی موردنظر را انتخاب کنید تا پلن‌های همان بخش نمایش داده شوند.',kb,c.message.message_id)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('shop_category:'))
def shop_category(c):
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    cid=int(c.data.split(':',1)[1])
    render_shop_products(c.message.chat.id,c.from_user.id,cid,c.message.message_id)
    bot.answer_callback_query(c.id)

def product_detail_text(p):
    return (
        f"<b>🛍 {safe(p['name'])}</b>\n"
        f"{safe(p['description'] or 'سرویس پایدار با تحویل خودکار')}\n\n"
        f"<b>مشخصات پلن</b>\n"
        f"▫️ حجم: <b>{p['traffic_gb']} GB</b>\n"
        f"▫️ اعتبار: <b>{p['days'] or 'نامحدود'} روز</b>\n"
        f"▫️ مبلغ: <b>{fmt(p['price'])}</b>\n\n"
        "در صورت داشتن کد تخفیف، قبل از تأیید خرید آن را اعمال کنید."
    )


def send_product_details(chat_id,pid,message_id=None,uid=None):
    uid=int(uid if uid is not None else chat_id)
    p=db.product(int(pid),active_only=False)
    if not p or (not p['active'] and (not p.get('is_custom') or int(p.get('custom_owner_id') or 0)!=uid)):
        return edit_or_send(chat_id,'این پلن دیگر در دسترس نیست.',home(uid))
    k = types.InlineKeyboardMarkup(row_width=2)
    k.add(types.InlineKeyboardButton('ادامه و تأیید خرید', callback_data=f'purchase_prepare:{p["id"]}'))
    if feature('discounts', '1'):
        k.add(types.InlineKeyboardButton('🎟 اعمال کد تخفیف', callback_data=f'discount_apply:{p["id"]}'))
    k.add(types.InlineKeyboardButton('⬅️ انتخاب پلن دیگر', callback_data='shop'))
    return edit_or_send(chat_id, product_detail_text(p), k, message_id)


def process_purchase(chat_id, uid, product_id, discount_code=None):
    if not feature('shop_enabled','1'):
        return edit_or_send(chat_id, 'فروشگاه در حال حاضر غیرفعال است.', home(uid))
    if maintenance(uid):
        return edit_or_send(chat_id, 'خرید در حال حاضر در دسترس نیست.', home(uid))
    p = db.product(int(product_id),active_only=False)
    if not p or (not p['active'] and (not p.get('is_custom') or int(p.get('custom_owner_id') or 0)!=int(uid))):
        return edit_or_send(chat_id, 'پلن پیدا نشد؛ لطفاً دوباره انتخاب کنید.', home(uid))
    if product_is_unlimited(p) and not feature('unlimited_plan','0'):
        return edit_or_send(chat_id,'پلن نامحدود در حال حاضر غیرفعال است.',home(uid))
    try:
        groups = json.loads(p.get('group_ids') or '[]')
        if not isinstance(groups, list) or any(type(group_id) is not int or group_id < 0 for group_id in groups):
            raise ValueError('Group IDs must be a JSON array of non-negative integers.')
    except (TypeError, ValueError) as exc:
        log.warning('Invalid group IDs for product %s: %s', product_id, exc)
        return edit_or_send(chat_id, 'تنظیمات گروه این پلن معتبر نیست؛ مدیر باید آن را اصلاح کند. مبلغی کسر نشد.', home(uid))
    reserved = False
    if discount_code:
        percent = db.reserve_discount(discount_code)
        if percent is None:
            return edit_or_send(chat_id, 'کد تخفیف تمام شده یا غیرفعال است؛ سفارش ثبت نشد.', home(uid))
        p = dict(p)
        p['price'] = int(p['price'] * (100 - percent) / 100)
        reserved = True
    try:
        oid = db.begin_order(uid, p, discount_code=discount_code)
    except Exception as exc:
        if reserved:
            db.release_discount(discount_code)
        return edit_or_send(chat_id, 'سفارش ثبت نشد: ' + safe(exc), home(uid))
    bot.send_message(chat_id, '⏳ <b>سفارش ثبت شد</b>\nدر حال اتصال به پنل و ساخت سرویس هستیم…')
    servers = db.servers()
    if not servers:
        refunded = db.fail_order_refund(oid, 'No active PasarGuard server')
        message = 'سرور فعالی تنظیم نشده است؛ مبلغ به کیف پول برگشت داده شد.' if refunded else 'سفارش به دلیل هم‌زمانی نیازمند بررسی مدیر است؛ مبلغ فعلاً محفوظ مانده است.'
        return edit_or_send(chat_id, message, home(uid))
    selected = None
    errors = []
    for server in servers:
        try:
            client = server_client(server)
            client.login()
            selected = (server, client)
            break
        except Exception as exc:
            errors.append(f"{server['name']}: {exc}")
            log.warning('Panel login failed: %s', exc)
    if not selected:
        refunded = db.fail_order_refund(oid, 'Panel login failed: ' + '; '.join(errors))
        message = 'اتصال به هیچ سرور فعالی موفق نبود؛ مبلغ برگشت داده شد.' if refunded else 'وضعیت سفارش نیازمند بررسی مدیر است؛ مبلغ فعلاً محفوظ مانده است.'
        return edit_or_send(chat_id, message, home(uid))
    server, client = selected
    username = f"sx{uid}_{secrets.token_hex(4)}"
    if not db.set_order_panel_target(oid, server['id'], username):
        refunded = db.fail_order_refund(oid, 'Could not persist panel target before remote create')
        message = 'ثبت اطلاعات مقصد سفارش ناموفق بود؛ مبلغ به کیف پول برگشت داده شد.' if refunded else 'سفارش به دلیل هم‌زمانی نیازمند بررسی مدیر است؛ مبلغ فعلاً محفوظ مانده است.'
        return edit_or_send(chat_id, message, home(uid))
    try:
        result, sub = client.create_user(username, p['traffic_gb'], p['days'], groups, note=f'SonarX order {oid}')
    except PasarGuardError as exc:
        if exc.uncertain:
            db.mark_order_review(oid, str(exc), server['id'], username)
            bot.send_message(chat_id, f'⚠️ نتیجه ساخت سرویس قطعی نیست؛ مبلغ محفوظ است و مدیر بررسی می‌کند. شناسه: <code>{oid[:8]}</code>')
            for aid in settings.admin_ids:
                try:
                    bot.send_message(aid, f'سفارش نیازمند بررسی: {oid}\nسرور: {safe(server["name"])}\nکاربر پنل: {safe(username)}\n{safe(exc)}')
                except Exception:
                    pass
        else:
            refunded = db.fail_order_refund(oid, str(exc), allow_review=True)
            edit_or_send(chat_id, 'ساخت سرویس ناموفق بود؛ مبلغ به کیف پول برگشت خورد.' if refunded else 'نتیجه سفارش نیازمند بررسی مدیر است؛ مبلغ فعلاً محفوظ مانده است.', home(uid))
        return
    except Exception as exc:
        db.mark_order_review(oid, f'Unexpected panel error; check panel before refund: {exc}', server['id'], username)
        log.exception('Uncertain panel create result')
        return bot.send_message(chat_id, '⚠️ نتیجه ساخت سرویس نامشخص است؛ مبلغ محفوظ است و سفارش برای بررسی مدیر ثبت شد.')
    try:
        db.finish_order(
            oid,server['id'],username,sub,
            cashback_percent=int(db.get_setting('cashback.purchase_percent','0') or 0) if feature('cashback','0') else 0,
            referral_percent=int(db.get_setting('referral.purchase_percent','0') or 0) if feature('reseller_commission','0') else -1
        )
    except Exception as exc:
        db.mark_order_review(oid, f'Panel user created; DB finalization failed: {exc}', server['id'], username)
        log.exception('Order finalization failed')
        return bot.send_message(chat_id, 'اکانت احتمالاً در پنل ساخته شده؛ ثبت دیتابیس نیازمند بررسی مدیر است و مبلغ محفوظ ماند.')
    success = (
        '<b>✅ سرویس شما آماده است</b>\n'
        f"پلن: <b>{safe(p['name'])}</b>\n"
        f"شناسه سفارش: <code>{oid[:8]}</code>\n"
        f"نام کاربری: <code>{safe(username)}</code>"
    )
    if feature('sub_link'):
        success+='\n\n<b>لینک اشتراک</b>\n'+f"<code>{safe(sub)}</code>\n\nلینک را کپی و در برنامه سازگار وارد کنید."
    if feature('cashback','0'):
        bonus=int(int(p['price'])*max(0,min(100,int(db.get_setting('cashback.purchase_percent','0') or 0)))/100)
        if bonus:success+=f"\n\n🎁 کش‌بک خرید: <b>{fmt(bonus)}</b>"
    edit_or_send(chat_id, success, home(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith('buy:'))
def buy(c):
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    if maintenance(c.from_user.id):
        return bot.answer_callback_query(c.id, 'ربات در حال تعمیرات است.', show_alert=True)
    p = db.product(int(c.data.split(':')[1]))
    if not p:
        return bot.answer_callback_query(c.id, 'پلن در دسترس نیست.', show_alert=True)
    if product_is_unlimited(p) and not feature('unlimited_plan','0'):
        return bot.answer_callback_query(c.id,'پلن نامحدود در حال حاضر غیرفعال است.',show_alert=True)
    send_product_details(c.message.chat.id,p['id'],c.message.message_id,c.from_user.id)
    bot.answer_callback_query(c.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('purchase_prepare:'))
def purchase_prepare(c):
    if not feature('shop_enabled','1'):
        return bot.answer_callback_query(c.id, 'فروشگاه در حال حاضر غیرفعال است.', show_alert=True)
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    if maintenance(c.from_user.id):
        return bot.answer_callback_query(c.id, 'ربات در حال تعمیرات است.', show_alert=True)
    pid = int(c.data.split(':', 1)[1])
    p = db.product(pid,active_only=False)
    if not p or (not p['active'] and (not p.get('is_custom') or int(p.get('custom_owner_id') or 0)!=int(c.from_user.id))):
        return bot.answer_callback_query(c.id, 'پلن پیدا نشد.', show_alert=True)
    if product_is_unlimited(p) and not feature('unlimited_plan','0'):
        return bot.answer_callback_query(c.id,'پلن نامحدود در حال حاضر غیرفعال است.',show_alert=True)
    quote = (
        '<b>🔐 تأیید نهایی سفارش</b>\n\n'
        f"پلن انتخابی: <b>{safe(p['name'])}</b>\n"
        f"حجم: {p['traffic_gb']} GB  |  اعتبار: {p['days'] or 'نامحدود'} روز\n"
        f"مبلغ قابل پرداخت: <b>{fmt(p['price'])}</b>\n\n"
        'با انتخاب «تأیید نهایی خرید»، مبلغ از کیف پول کسر و ساخت سرویس آغاز می‌شود.'
    )
    enter_reply_mode(
        c, quote,
        {'step': 'confirm_purchase', 'product_id': pid},
        rows=[(REPLY_CONFIRM_PURCHASE,), (REPLY_BACK_TO_PLAN, REPLY_CANCEL)],
        return_to='shop'
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith('discount_apply:'))
def discount_apply_start(c):
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    if maintenance(c.from_user.id):
        return bot.answer_callback_query(c.id, 'ربات در حال تعمیرات است.', show_alert=True)
    if not feature('discounts', '1'):
        return bot.answer_callback_query(c.id, 'کد تخفیف غیرفعال است.', show_alert=True)
    pid = int(c.data.split(':')[1])
    enter_reply_mode(c, '🎟 کد تخفیف را ارسال کنید.\nبرای خروج از این مرحله از دکمه‌های پایین استفاده کنید.',
                     {'step': 'discount_input', 'product_id': pid}, return_to='shop')


@bot.message_handler(func=lambda m: states.get(m.from_user.id, {}).get('step') == 'discount_input')
def discount_input(m):
    st = states.get(m.from_user.id, {})
    code = (m.text or '').strip().upper()
    d = db.get_discount(code)
    if not d:
        return bot.send_message(m.chat.id, 'کد تخفیف معتبر یا فعال نیست. دوباره تلاش کنید؛ یا «لغو عملیات» را بزنید.')
    p = db.product(st['product_id'],active_only=False)
    if not p or (not p['active'] and (not p.get('is_custom') or int(p.get('custom_owner_id') or 0)!=int(m.from_user.id))):
        states.pop(m.from_user.id, None)
        remove_reply_keyboard(m.chat.id, 'پلن پیدا نشد.')
        return edit_or_send(m.chat.id, home_text(m.from_user.id), home(m.from_user.id))
    price = int(p['price'] * (100 - d['percent']) / 100)
    st['step'] = 'confirm_purchase'
    st['discount_code'] = code
    prompt_reply_mode(
        m.chat.id, m.from_user.id,
        '<b>🎉 کد تخفیف اعمال شد</b>\n'
        f"کد: <code>{safe(code)}</code>  |  تخفیف: <b>{d['percent']}٪</b>\n"
        f"قیمت قبل: <s>{fmt(p['price'])}</s>\n"
        f"قیمت نهایی: <b>{fmt(price)}</b>\n\n"
        'برای ثبت سفارش، تأیید نهایی را انتخاب کنید.',
        st, rows=[(REPLY_CONFIRM_PURCHASE,), (REPLY_BACK_TO_PLAN, REPLY_CANCEL)], return_to='shop'
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith('confirm:'))
def confirm(c):
    # Backward-compatible callback for old Telegram messages.
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    if maintenance(c.from_user.id):
        return bot.answer_callback_query(c.id, 'ربات در حال تعمیرات است.', show_alert=True)
    pid = int(c.data.split(':')[1])
    discount_code = states.get(c.from_user.id, {}).get('discount_code')
    states.pop(c.from_user.id, None)
    bot.answer_callback_query(c.id, 'سفارش ثبت شد؛ در حال ساخت سرویس…')
    return process_purchase(c.message.chat.id, c.from_user.id, pid, discount_code)


def process_trial(chat_id,uid):
    if not feature('test_account','0'):
        return edit_or_send(chat_id,'اکانت تست در حال حاضر غیرفعال است.',home(uid))
    try:
        gb=int(db.get_setting('trial.gb','1') or 1)
        days=int(db.get_setting('trial.days','1') or 1)
        max_uses=int(db.get_setting('trial.max_per_user','1') or 1)
        groups=json.loads(db.get_setting('trial.group_ids','[]') or '[]')
        if gb<0 or gb>10000 or days<0 or days>3650 or (gb==0 and days==0) or not 1<=max_uses<=1000:
            raise ValueError('trial limits are outside the allowed range')
        if not isinstance(groups,list) or any(type(group_id) is not int or group_id<0 for group_id in groups):
            raise ValueError('trial group IDs must be a JSON array of non-negative integers')
    except (TypeError, ValueError) as exc:
        log.warning('Invalid trial configuration: %s',exc)
        return edit_or_send(chat_id,'تنظیمات اکانت تست معتبر نیست؛ با مدیریت تماس بگیرید.',home(uid))
    if (gb==0 or days==0) and not feature('unlimited_plan','0'):
        return edit_or_send(chat_id,'اکانت تست نامحدود غیرفعال است؛ با مدیریت تماس بگیرید.',home(uid))
    product=db.trial_product(gb,days,json.dumps(groups))
    servers=db.servers()
    preferred=db.get_setting('trial.server_id','')
    if preferred:
        servers=sorted(servers,key=lambda x:0 if str(x['id'])==str(preferred) else 1)
    if not servers:
        return edit_or_send(chat_id,'در حال حاضر سرور فعالی برای ساخت اکانت تست وجود ندارد.',home(uid))
    try:
        oid=db.begin_trial_order(uid,product,max_uses)
    except Exception as exc:
        return edit_or_send(chat_id,safe(exc),home(uid))
    selected=None;errors=[]
    for server in servers:
        try:
            client=server_client(server);client.login();selected=(server,client);break
        except Exception as exc:
            errors.append(f"{server['name']}: {exc}")
            log.warning('Trial panel login failed: %s',exc)
    if not selected:
        db.fail_order_refund(oid,'Trial panel login failed: '+'; '.join(errors))
        return edit_or_send(chat_id,'اتصال به سرور تست ناموفق بود؛ دوباره بعداً تلاش کنید.',home(uid))
    server,client=selected
    username=f"sxT{uid}_{secrets.token_hex(3)}"
    if not db.set_order_panel_target(oid,server['id'],username):
        db.fail_order_refund(oid,'Could not persist trial panel target before remote create')
        return edit_or_send(chat_id,'ثبت اطلاعات مقصد اکانت تست ناموفق بود؛ عملیات متوقف شد.',home(uid))
    try:
        result,sub=client.create_user(username,gb,days,groups,note=f'SonarX trial {oid}')
    except PasarGuardError as exc:
        if exc.uncertain:
            db.mark_order_review(oid,str(exc),server['id'],username)
            return edit_or_send(chat_id,'نتیجه ساخت اکانت تست نامشخص است؛ برای جلوگیری از ساخت تکراری، درخواست در صف بررسی قرار گرفت.',home(uid))
        refunded = db.fail_order_refund(oid,str(exc),allow_review=True)
        message = 'ساخت اکانت تست ناموفق بود: ' + safe(exc) if refunded else 'نتیجه سفارش تست نیازمند بررسی مدیر است؛ درخواست دوباره ثبت نکنید.'
        return edit_or_send(chat_id,message,home(uid))
    except Exception as exc:
        db.mark_order_review(oid,str(exc),server['id'],username)
        log.exception('Trial creation result uncertain')
        return edit_or_send(chat_id,'نتیجه ساخت اکانت تست نامشخص است و برای بررسی ثبت شد.',home(uid))
    try:
        db.finish_order(oid,server['id'],username,sub)
    except Exception as exc:
        db.mark_order_review(oid,'Trial panel user created; DB finalization failed: '+str(exc),server['id'],username)
        log.exception('Trial finalization failed')
        return edit_or_send(chat_id,'اکانت احتمالاً ساخته شده اما ثبت نهایی نیازمند بررسی مدیریت است.',home(uid))
    trial_text=f'🆓 <b>اکانت تست آماده شد</b>\nنام کاربری: <code>{safe(username)}</code>\nحجم: {gb} GB · اعتبار: {days} روز'
    if feature('sub_link'):trial_text+='\n\nلینک اشتراک:\n<code>'+safe(sub)+'</code>'
    return edit_or_send(chat_id,trial_text,home(uid))

@bot.callback_query_handler(func=lambda c:c.data=='trial')
def trial(c):
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    if maintenance(c.from_user.id):return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    if not feature('test_account','0'):return bot.answer_callback_query(c.id,'اکانت تست غیرفعال است.',show_alert=True)
    bot.answer_callback_query(c.id,'در حال ساخت اکانت تست...')
    process_trial(c.message.chat.id,c.from_user.id)

@bot.callback_query_handler(func=lambda c:c.data=='custom_plan')
def custom_plan_start(c):
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    if not feature('custom_plan','0'):return bot.answer_callback_query(c.id,'ساخت پلن دلخواه غیرفعال است.',show_alert=True)
    enter_reply_mode(c,
        '<b>🧩 ساخت پلن دلخواه</b>\nحجم و مدت موردنظر را به شکل <code>GB,روز</code> ارسال کنید.\nمثال: <code>20,30</code>',
        {'step':'custom_plan_input'},return_to='user_home')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='custom_plan_input')
def custom_plan_input(m):
    try:
        gb,days=[int(x.strip()) for x in (m.text or '').split(',')]
        min_gb=int(db.get_setting('custom.min_gb','1') or 1);max_gb=int(db.get_setting('custom.max_gb','500') or 500)
        min_days=int(db.get_setting('custom.min_days','1') or 1);max_days=int(db.get_setting('custom.max_days','365') or 365)
        if not min_gb<=gb<=max_gb or not min_days<=days<=max_days:
            raise ValueError(f'حجم باید بین {min_gb} تا {max_gb} GB و مدت بین {min_days} تا {max_days} روز باشد.')
        per_gb=int(db.get_setting('custom.price_per_gb','0') or 0)
        per_day=int(db.get_setting('custom.price_per_day','0') or 0)
        if per_gb<=0 or per_day<0:raise ValueError('قیمت‌گذاری پلن دلخواه هنوز توسط مدیریت تکمیل نشده است.')
        price=gb*per_gb+days*per_day
        if price<=0 or price>10**10:raise ValueError('مبلغ پلن سفارشی خارج از محدوده مجاز است.')
        groups=json.loads(db.get_setting('custom.group_ids','[]') or '[]')
        if not isinstance(groups,list) or any(type(group_id) is not int or group_id<0 for group_id in groups):
            raise ValueError('تنظیمات گروه‌های پلن سفارشی معتبر نیست.')
        product=db.add_custom_product(m.from_user.id,gb,days,price,json.dumps(groups))
        states.pop(m.from_user.id,None)
        remove_reply_keyboard(m.chat.id,'پیش‌فاکتور پلن دلخواه آماده شد.')
        return send_product_details(m.chat.id,product['id'],uid=m.from_user.id)
    except Exception as exc:
        return bot.send_message(m.chat.id,'ورودی یا تنظیمات نامعتبر است: '+safe(exc))

@bot.callback_query_handler(func=lambda c: c.data.startswith('service_addon:'))
def service_addon_start(c):
    _, kind, sid = c.data.split(':')
    if kind not in {'gb', 'days'}:
        return bot.answer_callback_query(c.id, 'عملیات نامعتبر است.')
    if kind == 'gb' and not feature('increase_volume', '0'):
        return bot.answer_callback_query(c.id, 'افزایش حجم غیرفعال است.', show_alert=True)
    if kind == 'days' and not feature('renew_service', '0'):
        return bot.answer_callback_query(c.id, 'تمدید سرویس غیرفعال است.', show_alert=True)
    service = db.service_for_user(int(sid), c.from_user.id)
    if not service:
        return bot.answer_callback_query(c.id, 'سرویس پیدا نشد.', show_alert=True)
    if service['panel_type'] == 'marzneshin':
        return bot.answer_callback_query(c.id, 'این عملیات برای پنل Marzneshin هنوز فعال نیست.', show_alert=True)
    unit = 'GB' if kind == 'gb' else 'روز'
    title = 'افزایش حجم' if kind == 'gb' else 'تمدید زمان'
    enter_reply_mode(c, f'<b>⚙️ {title}</b>\nمقدار موردنظر را به {unit} وارد کنید.\nپس از آن، مبلغ نهایی پیش از اجرا نمایش داده می‌شود.',
                     {'step': 'service_addon_value', 'kind': kind, 'service_id': int(sid)})


@bot.message_handler(func=lambda m: states.get(m.from_user.id, {}).get('step') == 'service_addon_value')
def service_addon_apply(m):
    st = states.get(m.from_user.id, {})
    try:
        units = int((m.text or '').strip())
        if not 1 <= units <= 3650:
            raise ValueError('مقدار باید بین ۱ تا ۳۶۵۰ باشد.')
        service = db.service_for_user(st['service_id'], m.from_user.id)
        if not service:
            raise ValueError('سرویس پیدا نشد.')
        price_key = 'addons.price_per_gb' if st['kind'] == 'gb' else 'addons.price_per_day'
        unit_price = int(db.get_setting(price_key, '0') or 0)
        if unit_price <= 0:
            raise ValueError('مدیر هنوز قیمت این خدمت را تنظیم نکرده است.')
        amount = unit_price * units
        st.update({'step': 'confirm_service_addon', 'units': units, 'amount': amount})
        unit = 'GB' if st['kind'] == 'gb' else 'روز'
        action = 'افزایش حجم' if st['kind'] == 'gb' else 'تمدید زمان'
        prompt_reply_mode(
            m.chat.id, m.from_user.id,
            f'<b>🧾 پیش‌نمایش تغییر سرویس</b>\n'
            f'عملیات: <b>{action}</b>\n'
            f'مقدار: <b>{units} {unit}</b>\n'
            f'مبلغ: <b>{fmt(amount)}</b>\n\n'
            'با تأیید، مبلغ از کیف پول کسر و درخواست برای پنل ارسال می‌شود.',
            st, rows=[(REPLY_CONFIRM_ADDON,), (REPLY_BACK, REPLY_CANCEL)], return_to='service_addon'
        )
    except ValueError as exc:
        bot.send_message(m.chat.id, 'ورودی یا تنظیمات نامعتبر است: ' + safe(exc))
    except Exception as exc:
        bot.send_message(m.chat.id, 'خطا در محاسبه هزینه: ' + safe(exc))


def process_service_addon(chat_id, uid, st):
    kind = st['kind']
    sid = st['service_id']
    units = st['units']
    amount = st['amount']
    service = db.service_for_user(sid, uid)
    if not service:
        return edit_or_send(chat_id, 'سرویس پیدا نشد.', home(uid))
    ref = f"addon:{sid}:{uid}:{secrets.token_hex(8)}"
    try:
        db.debit_wallet(uid, amount, ref)
    except Exception as exc:
        return edit_or_send(chat_id, 'پرداخت انجام نشد: ' + safe(exc), home(uid))
    try:
        try:
            client = server_client(service)
        except Exception:
            db.add_credit(uid, amount, 'refund:' + ref, kind='refund')
            return edit_or_send(chat_id, 'تنظیمات اتصال پنل معتبر نیست؛ مبلغ به کیف پول برگشت داده شد.', home(uid))
        result, new_limit, new_expire = client.extend_user(
            service['remote_username'],
            add_gb=units if kind == 'gb' else 0,
            add_days=units if kind == 'days' else 0
        )
        remote_status = str(result.get('status') or '').lower() if isinstance(result, dict) else ''
        enabled_text = str(result.get('enabled')).strip().lower() if isinstance(result, dict) and result.get('enabled') is not None else ''
        if enabled_text in {'0','false','no','off'} or remote_status in {'disabled','disable','inactive','on_hold'}:
            remote_status = 'disabled'
        elif remote_status in {'online','enabled'} or (not remote_status and enabled_text in {'1','true','yes','on'}):
            remote_status = 'active'
        if remote_status not in {'active','expired','limited','disabled'}:
            remote_status = UNSET
        snapshot_ok = db.update_service_entitlements(sid, uid, new_limit, new_expire, status=remote_status)
        if not snapshot_ok:
            db.audit(uid, 'service.addon.snapshot_review', str(sid), f'{kind}={units};amount={amount};ref={ref}')
            for aid in settings.admin_ids:
                try:
                    bot.send_message(aid, f'⚠️ تغییر پنل برای سرویس {sid} اعمال شد اما همگام‌سازی دیتابیس ناموفق بود. کاربر={uid}؛ بررسی دستی لازم است.')
                except Exception:
                    pass
            return edit_or_send(chat_id, 'تغییر در پنل انجام شده، اما ثبت اطلاعات محلی کامل نشد؛ درخواست برای بررسی مدیر ثبت شد. لطفاً دوباره خرید نکنید.', home(uid))
        db.audit(uid, 'service.addon', str(sid), f'{kind}={units};amount={amount}')
        return edit_or_send(chat_id,
            f'<b>✅ تغییر سرویس انجام شد</b>\nمبلغ: {fmt(amount)}\nموجودی باقی‌مانده: {fmt(db.get_user(uid)["balance"])}',
            home(uid))
    except ValueError as exc:
        db.add_credit(uid, amount, 'refund:' + ref, kind='refund')
        return edit_or_send(chat_id, 'این سرویس قابل تغییر نیست؛ مبلغ به کیف پول برگشت داده شد.\n' + safe(exc), home(uid))
    except PasarGuardError as exc:
        if not exc.uncertain:
            db.add_credit(uid, amount, 'refund:' + ref, kind='refund')
            return edit_or_send(chat_id, 'پنل تغییر را نپذیرفت؛ مبلغ به کیف پول برگشت داده شد.\n' + safe(exc), home(uid))
        db.audit(uid, 'service.addon.review', str(sid), str(exc))
        for aid in settings.admin_ids:
            try:
                remove_reply_keyboard(aid, f'تغییر سرویس نیازمند بررسی است. user={uid} service={sid} ref={ref}\n{safe(exc)}')
            except Exception:
                pass
        return edit_or_send(chat_id, 'نتیجه تغییر در پنل نامشخص است؛ مبلغ فعلاً محفوظ مانده و برای بررسی مدیر ارسال شد.', home(uid))
    except Exception as exc:
        db.audit(uid, 'service.addon.error', str(sid), str(exc))
        log.exception('Unexpected service addon error')
        return edit_or_send(chat_id, 'خطای غیرمنتظره رخ داد؛ برای جلوگیری از بازپرداخت اشتباه، مبلغ تا بررسی مدیر محفوظ است.', home(uid))


@bot.callback_query_handler(func=lambda c: c.data == 'profile')
def profile(c):
    if not feature('account_info'):return bot.answer_callback_query(c.id,'نمایش اطلاعات حساب غیرفعال است.',show_alert=True)
    u = db.get_user(c.from_user.id)
    if not u:
        return bot.answer_callback_query(c.id, 'حساب کاربری پیدا نشد.')
    status = '🔴 مسدود' if u['is_blocked'] else '🟢 فعال'
    phone_status='🟢 تأیید شده' if u.get('phone_verified') else '⚪ تأیید نشده'
    text = (
        '<b>👤 حساب کاربری شما</b>\n'
        f"شناسه تلگرام: <code>{u['id']}</code>\n"
        f"موجودی قابل استفاده: <b>{fmt(u['balance'])}</b>\n"
        f"وضعیت حساب: {status}\n"
        f"تأیید شماره تلفن: {phone_status}\n\n"
        'از گزینه‌های زیر برای مدیریت حساب استفاده کنید.'
    )
    k = types.InlineKeyboardMarkup(row_width=2)
    k.add(types.InlineKeyboardButton('💳 افزایش موجودی', callback_data='deposit'), types.InlineKeyboardButton('📦 سرویس‌های من', callback_data='orders'))
    k.add(types.InlineKeyboardButton('🏠 منوی اصلی', callback_data='user_home'))
    edit_or_send(c.message.chat.id, text, k, c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == 'orders')
def orders(c):
    rows = db.services_for(c.from_user.id)
    if not rows:
        old_orders = db.orders_for(c.from_user.id)
        if not old_orders:
            return edit_or_send(c.message.chat.id, '<b>📦 سرویس‌های من</b>\nهنوز سرویسی ثبت نکرده‌اید. از فروشگاه می‌توانید اولین سرویس را تهیه کنید.', home(c.from_user.id), c.message.message_id)
        lines = [f"سفارش <code>{o['id'][:8]}</code> · {safe(o['product_name'])} · {safe(o['status'])}" for o in old_orders[:10]]
        return edit_or_send(c.message.chat.id, '<b>📦 تاریخچه سفارش‌ها</b>\n\n' + '\n'.join(lines), home(c.from_user.id), c.message.message_id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for index, service in enumerate(rows[:20], 1):
        icon = '🟢' if service['status'] == 'active' else '⚪'
        kb.add(types.InlineKeyboardButton(f"{icon} {service['product_name'][:28]} · {service['server_name'][:18]}", callback_data=f"service_detail:{service['id']}"))
    kb.add(types.InlineKeyboardButton('🏠 منوی اصلی', callback_data='user_home'))
    text = (
        '<b>📦 سرویس‌های من</b>\n'
        f'تعداد سرویس‌ها: <b>{len(rows)}</b>\n\n'
        'برای مشاهده لینک اشتراک، وضعیت، مصرف یا تمدید، سرویس را انتخاب کنید.'
    )
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('service_detail:'))
def service_detail(c):
    service_id=int(c.data.split(':',1)[1])
    service=db.service_for_user(service_id,c.from_user.id)
    if not service:return bot.answer_callback_query(c.id,'سرویس پیدا نشد.',show_alert=True)
    kb=types.InlineKeyboardMarkup(row_width=2)
    actions=[]
    if feature('qr_code') and service.get('subscription_url'):
        actions.append(types.InlineKeyboardButton('▦ دریافت QR',callback_data=f'service_qr:{service_id}'))
    if feature('increase_volume','0'):
        actions.append(types.InlineKeyboardButton('➕ افزایش حجم',callback_data=f'service_addon:gb:{service_id}'))
    if feature('renew_service','0'):
        actions.append(types.InlineKeyboardButton('🗓 تمدید زمان',callback_data=f'service_addon:days:{service_id}'))
    if actions:kb.row(*actions)
    if feature('service_status'):
        kb.add(types.InlineKeyboardButton('🔄 بروزرسانی از پنل',callback_data=f'service_refresh:{service_id}'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به فهرست سرویس‌ها',callback_data='orders'))
    edit_or_send(c.message.chat.id,service_detail_text(service),kb,c.message.message_id)


def service_detail_text(service):
    used=int(service['used_bytes'] or 0)/1024**3
    limit=int(service['data_limit_bytes'] or 0)/1024**3 if service['data_limit_bytes'] else 0
    status='🟢 فعال' if service['status']=='active' else '⚪ '+safe(service['status'])
    text=(f"<b>📦 {safe(service['product_name'])}</b>\nوضعیت: {status}\n"
          f"سرور: {safe(service['server_name'])}\nمصرف: <b>{used:.2f} GB</b> از <b>{limit:.2f} GB</b>\n")
    if service['expires_at']:text+=f"اعتبار تا: <b>{safe(service['expires_at'])}</b>\n"
    if service['subscription_url'] and feature('sub_link'):text+=f"\n<b>لینک اشتراک</b>\n<code>{safe(service['subscription_url'])}</code>"
    return text

@bot.callback_query_handler(func=lambda c:c.data.startswith('service_refresh:'))
def service_refresh(c):
    sid=int(c.data.split(':',1)[1])
    service=db.service_for_user(sid,c.from_user.id)
    if not service:return bot.answer_callback_query(c.id,'سرویس پیدا نشد.',show_alert=True)
    try:
        client=server_client(service);raw=client.get_user(service['remote_username'])
        used_value=raw.get('used_traffic')
        if used_value is None:used_value=raw.get('used_bytes',0)
        used=int(used_value or 0)
        limit_value=raw.get('data_limit')
        if limit_value is None:limit_value=raw.get('totalGB',0)
        limit=int(limit_value or 0)
        expiry_keys=('expire','expire_date','expires_at','expiryTime')
        expiry_entries=[(key,raw[key]) for key in expiry_keys if key in raw]
        if not expiry_entries:
            expires_at=service['expires_at']
        else:
            selected=next(((key,value) for key,value in expiry_entries if value not in (None,0,'0','')),None)
            if selected is None:
                expires_at=None
            else:
                expiry_key,expiry_value=selected
                if expiry_key=='expiryTime':expiry_value=float(expiry_value)/1000
                parsed=client._as_datetime(expiry_value) if hasattr(client,'_as_datetime') else None
                expires_at=parsed.isoformat() if parsed else service['expires_at']
        raw_status=raw.get('status')
        status=str(raw_status or service['status']).lower()
        enabled_text=str(raw.get('enabled')).strip().lower() if raw.get('enabled') is not None else ''
        if status == 'expired':
            pass
        elif enabled_text in {'0','false','no','off'}:status='disabled'
        elif status in {'online','enabled'} or (not raw_status and enabled_text in {'1','true','yes','on'}):
            status='active'
        elif status in {'inactive','on_hold','disable'}:
            status='disabled'
        elif status not in {'active','expired','limited','disabled'}:
            status=service['status']
        sub=client.subscription_url(raw,service['remote_username']) if hasattr(client,'subscription_url') else client._sub(raw)
        db.update_service_snapshot(sid,c.from_user.id,used,limit,expires_at,status,subscription_url=sub)
        service=db.service_for_user(sid,c.from_user.id)
        bot.answer_callback_query(c.id,'اطلاعات سرویس از پنل به‌روزرسانی شد.')
        kb=types.InlineKeyboardMarkup(row_width=2)
        if feature('qr_code') and service.get('subscription_url'):
            kb.add(types.InlineKeyboardButton('▦ دریافت QR',callback_data=f'service_qr:{sid}'))
        if feature('increase_volume','0'):kb.add(types.InlineKeyboardButton('➕ افزایش حجم',callback_data=f'service_addon:gb:{sid}'))
        if feature('renew_service','0'):kb.add(types.InlineKeyboardButton('🗓 تمدید زمان',callback_data=f'service_addon:days:{sid}'))
        kb.add(types.InlineKeyboardButton('🔄 بروزرسانی مجدد',callback_data=f'service_refresh:{sid}'))
        kb.add(types.InlineKeyboardButton('⬅️ فهرست سرویس‌ها',callback_data='orders'))
        return edit_or_send(c.message.chat.id,service_detail_text(service),kb,c.message.message_id)
    except Exception as exc:
        bot.answer_callback_query(c.id,'دریافت اطلاعات از پنل ناموفق بود.',show_alert=True)
        log.warning('Service refresh failed: %s',exc)

@bot.callback_query_handler(func=lambda c:c.data.startswith('service_qr:'))
def service_qr(c):
    sid=int(c.data.split(':',1)[1])
    service=db.service_for_user(sid,c.from_user.id)
    if not service or not service.get('subscription_url'):
        return bot.answer_callback_query(c.id,'لینک اشتراک در دسترس نیست.',show_alert=True)
    if not feature('qr_code'):return bot.answer_callback_query(c.id,'نمایش QR غیرفعال است.',show_alert=True)
    try:
        import qrcode
        image=qrcode.make(service['subscription_url'])
        stream=io.BytesIO();stream.name='sonarx-qr.png';image.save(stream,format='PNG');stream.seek(0)
        bot.send_photo(c.message.chat.id,stream,caption=f"QR اشتراک سرویس <b>{safe(service['product_name'])}</b>")
        bot.answer_callback_query(c.id,'QR آماده شد.')
    except ImportError:
        bot.answer_callback_query(c.id,'کتابخانه QR نصب نیست؛ requirements را بروزرسانی کنید.',show_alert=True)
    except Exception as exc:
        log.exception('QR generation failed')
        bot.answer_callback_query(c.id,'ساخت QR ناموفق بود.',show_alert=True)

@bot.callback_query_handler(func=lambda c:c.data=='deposit')
def deposit(c):
    if not access_gate(c.from_user.id,c.message.chat.id,c):return
    if maintenance(c.from_user.id):return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    if not feature('deposits_enabled'):return bot.answer_callback_query(c.id,'شارژ کیف پول غیرفعال است.',show_alert=True)
    if not feature('manual_receipt','1'):return bot.answer_callback_query(c.id,'ثبت رسید دستی غیرفعال است.',show_alert=True)
    enter_reply_mode(c, '<b>💳 افزایش موجودی</b>\n' + safe(text_setting('payment', 'مبلغ شارژ را وارد کنید؛ سپس تصویر رسید را ارسال کنید.')) + '\n\nبرای لغو یا بازگشت از دکمه‌های پایین استفاده کنید.', {'step':'deposit_amount'})

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='deposit_amount')
def deposit_amount(m):
    try:
        amount=int((m.text or '').replace(',','').strip())
        if amount<=0 or amount>10**10:raise ValueError()
        prompt_reply_mode(m.chat.id, m.from_user.id, '<b>📎 ارسال رسید پرداخت</b>\nتصویر واضح رسید را ارسال کنید. مبلغ ثبت‌شده: <b>' + fmt(amount) + '</b>\n\nبرای لغو از دکمه پایین استفاده کنید.', {'step':'deposit_receipt','amount':amount})
    except Exception:bot.send_message(m.chat.id,'مبلغ معتبر نیست؛ عدد مثبت وارد کنید.')

@bot.message_handler(content_types=['photo'],func=lambda m:states.get(m.from_user.id,{}).get('step')=='deposit_receipt')
def deposit_receipt(m):
    st=states.pop(m.from_user.id);did=db.create_deposit(m.from_user.id,st['amount'],m.photo[-1].file_id)
    remove_reply_keyboard(m.chat.id, 'رسید شما با موفقیت ثبت شد.')
    edit_or_send(m.chat.id, f'<b>✅ رسید ثبت شد</b>\nشماره پیگیری: <code>{did}</code>\nوضعیت: در انتظار بررسی مدیریت', home(m.from_user.id))
    k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🔎 بررسی رسید',callback_data=f'dep_review:{did}'))
    for aid in settings.admin_ids:
        try:
            remove_reply_keyboard(aid, 'رسید جدید برای بررسی ثبت شد.')
            clear_chat_inline(aid)
            sent = bot.send_photo(aid,m.photo[-1].file_id,caption=f'رسید #{did}\nکاربر: {m.from_user.id}\nمبلغ: {fmt(st["amount"])}',reply_markup=k)
            register_inline_message(aid, getattr(sent, 'message_id', None))
        except Exception:log.exception('Receipt notification failed')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='deposit_receipt')
def deposit_receipt_hint(m):
    bot.send_message(m.chat.id, 'لطفاً تصویر رسید را به‌صورت عکس ارسال کنید؛ برای خروج از «لغو عملیات» استفاده کنید.')


@bot.callback_query_handler(func=lambda c: c.data.startswith('dep_review:'))
def deposit_review_start(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    did = int(c.data.split(':', 1)[1])
    deposit = db.deposit(did)
    if not deposit or deposit['status'] != 'pending':
        return bot.answer_callback_query(c.id, 'این رسید قبلاً تعیین تکلیف شده است.', show_alert=True)
    user = db.get_user(deposit['user_id']) or {}
    text = (
        f'<b>🧾 بررسی رسید #{did}</b>\n'
        f"کاربر: <code>{deposit['user_id']}</code> · @{safe(user.get('username') or '-')}\n"
        f"مبلغ: <b>{fmt(deposit['amount'])}</b>\n"
        f"تاریخ ثبت: <code>{safe(deposit['created_at'])}</code>\n\n"
        'ابتدا تصویر رسید را بررسی کنید؛ سپس نوع تصمیم را انتخاب کنید.'
    )
    enter_reply_mode(c, text, {'step':'review_deposit','deposit_id':did},
                     rows=[(REPLY_APPROVE_DEPOSIT, REPLY_REJECT_DEPOSIT), (REPLY_BACK, REPLY_CANCEL)],
                     return_to='admin_section:finance')


def process_deposit_decision(chat_id, admin_id, deposit_id, decision):
    deposit = db.decide_deposit(
        int(deposit_id),decision=='approve',admin_id,
        cashback_percent=int(db.get_setting('cashback.deposit_percent','0') or 0) if feature('cashback','0') else 0
    )
    if not deposit:
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به مالی', callback_data='admin_section:finance'))
        return edit_or_send(chat_id, 'این رسید قبلاً تعیین تکلیف شده است.', kb)
    db.audit(admin_id, 'deposit.decide', str(deposit_id), decision)
    try:
        if decision == 'approve':
            cashback_pct=int(db.get_setting('cashback.deposit_percent','0') or 0) if feature('cashback','0') else 0
            cashback=int(int(deposit['amount'])*max(0,min(100,cashback_pct))/100)
            suffix=f'\nکش‌بک: {fmt(cashback)}' if cashback else ''
            bot.send_message(deposit['user_id'], f'✅ پرداخت شما تأیید شد و {fmt(deposit["amount"])} به کیف پول افزوده شد.{suffix}')
        else:
            bot.send_message(deposit['user_id'], '❌ رسید پرداخت شما تأیید نشد. در صورت نیاز، تیکت پشتیبانی ثبت کنید.')
    except Exception:
        pass
    text = f"{'✅ شارژ تأیید شد' if decision == 'approve' else '❌ رسید رد شد'}\nرسید <code>#{deposit_id}</code> تعیین تکلیف شد."
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('مشاهده رسیدهای معلق', callback_data='admin_deposits'))
    kb.add(types.InlineKeyboardButton('بازگشت به مالی', callback_data='admin_section:finance'))
    return edit_or_send(chat_id, text, kb)


@bot.callback_query_handler(func=lambda c:c.data.startswith('dep:'))
def decide_deposit(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    _,decision,did=c.data.split(':')
    d=db.decide_deposit(int(did),decision=='yes',c.from_user.id,
        cashback_percent=int(db.get_setting('cashback.deposit_percent','0') or 0) if feature('cashback','0') else 0)
    if not d:return bot.answer_callback_query(c.id,'این رسید قبلاً تعیین تکلیف شده است.',show_alert=True)
    db.audit(c.from_user.id,'deposit.decide',did,decision)
    bot.answer_callback_query(c.id,'ثبت شد.')
    try:
        if decision=='yes':
            cashback_pct=int(db.get_setting('cashback.deposit_percent','0') or 0) if feature('cashback','0') else 0
            cashback=int(int(d['amount'])*max(0,min(100,cashback_pct))/100)
            note=f'شارژ کیف پول تأیید شد: {fmt(d["amount"])}'+(f' · کش‌بک: {fmt(cashback)}' if cashback else '')
        else:note='رسید پرداخت رد شد.'
        bot.send_message(d['user_id'],note)
    except Exception:pass
    try:
        bot.edit_message_caption((c.message.caption or '')+'\n\n✅ تعیین تکلیف شد.',c.message.chat.id,c.message.message_id,reply_markup=None)
        inline_message_ids.get(c.message.chat.id, set()).discard(c.message.message_id)
    except Exception:pass

@bot.callback_query_handler(func=lambda c:c.data=='support')
def support(c):
    if not feature('support_enabled'):return bot.answer_callback_query(c.id,'پشتیبانی موقتاً غیرفعال است.',show_alert=True)
    enter_reply_mode(c, '<b>🎫 ثبت تیکت پشتیبانی</b>\nدر یک جمله موضوع درخواست خود را بنویسید.\n\nبرای لغو یا بازگشت از دکمه‌های پایین استفاده کنید.', {'step':'ticket_subject'})

@bot.message_handler(func=lambda m: states.get(m.from_user.id, {}).get('step') == 'user_ticket_reply')
def user_ticket_reply(m):
    st = states.get(m.from_user.id, {})
    body = (m.text or '').strip()
    if len(body) < 2:
        return bot.send_message(m.chat.id, 'پیام کوتاه است؛ لطفاً متن کامل‌تری ارسال کنید.')
    ticket = next((item for item in db.tickets_for(m.from_user.id) if item['id'] == st.get('ticket_id') and item['status'] == 'open'), None)
    if not ticket:
        return finish_reply_mode(m.chat.id, m.from_user.id, 'این تیکت دیگر باز نیست.')
    db.reply_ticket(st['ticket_id'], m.from_user.id, body)
    for aid in settings.admin_ids:
        try:
            remove_reply_keyboard(aid, f'پیام جدید برای تیکت #{st["ticket_id"]} ثبت شد.')
            clear_chat_inline(aid)
            sent = bot.send_message(aid, f'💬 پیام جدید کاربر {m.from_user.id} در تیکت #{st["ticket_id"]}.', reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('مشاهده تیکت', callback_data=f'ticket:{st["ticket_id"]}')))
            register_inline_message(aid, getattr(sent, 'message_id', None))
        except Exception:
            pass
    finish_reply_mode(m.chat.id, m.from_user.id, 'پیام شما به تیکت اضافه شد.')


@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='ticket_subject')
def ticket_subject(m):
    subject = (m.text or '').strip()
    if len(subject) < 3:
        return bot.send_message(m.chat.id, 'موضوع باید حداقل ۳ نویسه باشد؛ دوباره وارد کنید.')
    prompt_reply_mode(m.chat.id, m.from_user.id, '<b>✍️ شرح درخواست</b>\nجزئیات مشکل را بنویسید؛ در صورت نیاز مدل دستگاه و پیام خطا را هم ذکر کنید.', {'step':'ticket_body','subject':subject[:180]})

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='ticket_body')
def ticket_body(m):
    body = (m.text or '').strip()
    if len(body) < 5:
        return bot.send_message(m.chat.id, 'شرح درخواست کوتاه است؛ لطفاً حداقل ۵ نویسه توضیح بنویسید.')
    st=states.pop(m.from_user.id);tid=db.add_ticket(m.from_user.id,st['subject'],body)
    remove_reply_keyboard(m.chat.id, 'تیکت شما ثبت شد.')
    edit_or_send(m.chat.id, f'<b>✅ تیکت ثبت شد</b>\nشماره پیگیری: <code>#{tid}</code>\nپاسخ پشتیبانی از همین گفتگو برای شما ارسال می‌شود.', home(m.from_user.id))
    for aid in settings.admin_ids:
        try:
            remove_reply_keyboard(aid, 'یک تیکت جدید برای بررسی ثبت شده است.')
            clear_chat_inline(aid)
            sent = bot.send_message(aid,f'🎫 تیکت جدید #{tid} از کاربر {m.from_user.id}: {safe(st["subject"])}',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('مشاهده تیکت',callback_data=f'ticket:{tid}')))
            register_inline_message(aid, getattr(sent, 'message_id', None))
        except Exception:pass

@bot.callback_query_handler(func=lambda c: c.data == 'admin')
def admin_entry(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    admin_panel(c.message.chat.id, c.message.message_id, uid=c.from_user.id)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data == 'stats')
def stats(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    data = db.stats()
    text = (
        '<b>📊 داشبورد زنده SonarX</b>\n'
        '<i>نمای کلی وضعیت کسب‌وکار</i>\n\n'
        f"👥 کاربران ثبت‌شده: <b>{data['users']:,}</b>\n"
        f"🛒 سفارش‌های موفق: <b>{data['orders']:,}</b>\n"
        f"💰 فروش ثبت‌شده: <b>{fmt(data['revenue'])}</b>\n"
        f"🧾 رسیدهای در انتظار: <b>{data['pending_deposits']:,}</b>\n"
        f"🎫 تیکت‌های باز: <b>{data['open_tickets']:,}</b>"
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به نمای کلی', callback_data='admin_section:overview'))
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == 'admin_config')
def admin_config(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(types.InlineKeyboardButton('⚙️ وضعیت عملیات', callback_data='admin_config_toggles'),
           types.InlineKeyboardButton('✏️ متن‌های ربات', callback_data='admin_config_texts'))
    kb.add(types.InlineKeyboardButton('🎚 ماتریس همه قابلیت‌ها', callback_data='feature_matrix:0'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به ظاهر و تجربه کاربری', callback_data='admin_section:interface'))
    text = '<b>🧩 تنظیمات عمومی</b>\nتنظیمات را بر اساس نوع کار جدا کرده‌ایم تا صفحه شلوغ نشود.'
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == 'admin_config_toggles')
def admin_config_toggles(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    toggles = [('shop_enabled','فروشگاه'),('deposits_enabled','پرداخت و شارژ'),('support_enabled','پشتیبانی'),('maintenance','حالت تعمیرات'),('channel_membership','عضویت اجباری'),('auto_renew','تمدید خودکار'),('traffic_sync','همگام‌سازی مصرف'),('backup','پشتیبان‌گیری'),('rate_limit','محدودیت درخواست'),('anti_fraud','ضدتقلب')]
    buttons = []
    for key, label in toggles:
        value = db.get_setting('feature.' + key, feature_default(key))
        icon = ('🟢' if value == '1' else '⚪') if key in OPERATIONAL_FEATURES else '🧩'
        buttons.append(types.InlineKeyboardButton(f'{icon} {label}', callback_data='cfg:' + key))
    kb = types.InlineKeyboardMarkup(row_width=2)
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i+2])
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به تنظیمات عمومی', callback_data='admin_config'))
    text = '<b>⚙️ وضعیت عملیات</b>\n🟢 فعال · ⚪ غیرفعال · 🧩 ماژول اجرایی هنوز منتقل نشده و قابل فعال‌سازی نیست.'
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == 'admin_config_texts')
def admin_config_texts(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    texts = [('welcome','خوش‌آمدگویی'),('payment','راهنمای پرداخت'),('shop_notice','پیام فروشگاه'),('faq','سوالات متداول'),('tutorials','آموزش اتصال'),('rules','قوانین ربات')]
    buttons = [types.InlineKeyboardButton('✏️ ' + label, callback_data='edittext:' + key) for key, label in texts]
    kb = types.InlineKeyboardMarkup(row_width=2)
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i+2])
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به تنظیمات عمومی', callback_data='admin_config'))
    text = '<b>✏️ متن‌های ربات</b>\nهر متن را جداگانه باز کنید، ویرایش کنید و ذخیره کنید.'
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c:c.data.startswith('cfg:'))
def config_toggle(c):
    if not admin(c.from_user.id):return
    key=c.data.split(':',1)[1]
    if key not in OPERATIONAL_FEATURES:
        return bot.answer_callback_query(c.id,'این قابلیت هنوز ماژول اجرایی ندارد و در این نسخه قابل فعال‌سازی نیست.',show_alert=True)
    dbkey='feature.'+key;default=feature_default(key)
    new='0' if db.get_setting(dbkey,default)=='1' else '1';db.set_setting(dbkey,new);db.audit(c.from_user.id,'setting.toggle',dbkey,new)
    bot.answer_callback_query(c.id,'تنظیم ذخیره شد.');admin_config_toggles(c)

@bot.callback_query_handler(func=lambda c:c.data.startswith('edittext:'))
def edit_text(c):
    if not admin(c.from_user.id):return
    key = c.data.split(':', 1)[1]
    enter_reply_mode(c, f'<b>✏️ ویرایش متن: {safe(key)}</b>\nمتن جدید را در یک پیام ارسال کنید.', {'step':'set_text','key':key}, return_to='admin_section:interface')

@bot.callback_query_handler(func=lambda c: c.data == 'admin_products')
def admin_products(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    products = db.products_admin()
    kb = types.InlineKeyboardMarkup(row_width=1)
    lines = []
    for item in products:
        icon = '🟢' if item['active'] else '⚪'
        lines.append(f"{icon} <b>{safe(item['name'])}</b> · {fmt(item['price'])}")
        kb.add(types.InlineKeyboardButton(f"{icon} {item['name'][:30]} · {fmt(item['price'])}", callback_data=f"product_manage:{item['id']}"))
    kb.add(types.InlineKeyboardButton('➕ ساخت پلن جدید', callback_data='add_product'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به فروشگاه و سرویس‌ها', callback_data='admin_section:store'))
    text = '<b>📦 مدیریت پلن‌ها</b>\nبرای ویرایش وضعیت یا قیمت، پلن را انتخاب کنید.\n\n' + ('\n'.join(lines) if lines else 'هنوز پلنی ایجاد نشده است.')
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('product_manage:'))
def product_manage(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    pid = int(c.data.split(':', 1)[1])
    item = db.product(pid, False)
    if not item:
        return bot.answer_callback_query(c.id, 'پلن پیدا نشد.')
    status = '🟢 فعال' if item['active'] else '⚪ غیرفعال'
    text = (
        f"<b>📦 {safe(item['name'])}</b>\n"
        f"وضعیت: {status}\n"
        f"قیمت: <b>{fmt(item['price'])}</b>\n"
        f"حجم: {item['traffic_gb']} GB · اعتبار: {item['days'] or 'نامحدود'} روز\n\n"
        f"{safe(item['description'] or '')}"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('🔄 تغییر وضعیت', callback_data=f'ptoggle:{pid}'), types.InlineKeyboardButton('✏️ تغییر قیمت', callback_data=f'pprice:{pid}'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به فهرست پلن‌ها', callback_data='admin_products'))
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c:c.data.startswith('pprice:'))
def product_price_start(c):
    if not admin(c.from_user.id):return
    pid=int(c.data.split(':')[1]);p=db.product(pid,False)
    if not p:return bot.answer_callback_query(c.id,'پلن پیدا نشد.')
    enter_reply_mode(c, f"<b>✏️ تغییر قیمت پلن {safe(p['name'])}</b>\nقیمت جدید را به {settings.currency_label} ارسال کنید.", {'step':'product_price','product_id':pid}, return_to='admin_section:store')

@bot.callback_query_handler(func=lambda c:c.data.startswith('ptoggle:'))
def product_toggle(c):
    if not admin(c.from_user.id):return
    pid=int(c.data.split(':')[1]);p=db.product(pid,False)
    if not p:return bot.answer_callback_query(c.id,'پلن پیدا نشد.')
    db.set_product_active(pid,not bool(p['active']));db.audit(c.from_user.id,'product.toggle',str(pid),str(not bool(p['active'])))
    bot.answer_callback_query(c.id,'وضعیت تغییر کرد.');admin_products(c)

@bot.callback_query_handler(func=lambda c:c.data=='admin_servers')
def admin_servers(c):
    if not admin(c.from_user.id):return
    k=types.InlineKeyboardMarkup(row_width=1)
    for s in db.servers_admin():
        k.add(types.InlineKeyboardButton(f"{s['name']} | {s['panel_type']} | {'فعال' if s['active'] else 'غیرفعال'}",callback_data=f"server_details:{s['id']}"))
    k.add(types.InlineKeyboardButton('➕ افزودن سرور جدید',callback_data='add_server'))
    k.add(types.InlineKeyboardButton('⬅️ بازگشت به فروشگاه و سرویس‌ها',callback_data='admin_section:store'))
    text = '<b>🖥 سرورها و پنل‌ها</b>\nبرای مشاهده جزئیات یا تست اتصال، یک سرور را انتخاب کنید.'
    edit_or_send(c.message.chat.id, text, k, c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('server_details:'))
def server_details(c):
    if not admin(c.from_user.id):return
    sid=int(c.data.split(':')[1]);s=next((x for x in db.servers_admin() if x['id']==sid),None)
    if not s:return bot.answer_callback_query(c.id,'سرور پیدا نشد.')
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('🔌 تست اتصال',callback_data=f'server_test:{sid}'),types.InlineKeyboardButton('فعال/غیرفعال',callback_data=f'stoggle:{sid}'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به فهرست سرورها',callback_data='admin_servers'))
    text = (f"<b>🖥 {safe(s['name'])}</b>\nنوع پنل: <b>{safe(s['panel_type'])}</b>\nآدرس: <code>{safe(s['url'])}</code>\nاولویت: {s['priority']}\nوضعیت: {'🟢 فعال' if s['active'] else '⚪ غیرفعال'}\n\nاطلاعات ورود محرمانه است و نمایش داده نمی‌شود.")
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('server_test:'))
def server_test(c):
    if not admin(c.from_user.id):return
    sid=int(c.data.split(':')[1]);s=next((x for x in db.servers_admin() if x['id']==sid),None)
    if not s:return bot.answer_callback_query(c.id,'سرور پیدا نشد.')
    try:
        client=server_client(s);client.login()
        db.audit(c.from_user.id,'server.test',str(sid),'ok')
        bot.answer_callback_query(c.id,'اتصال موفق است.',show_alert=True)
    except Exception as exc:
        db.audit(c.from_user.id,'server.test',str(sid),str(exc)[:300])
        bot.answer_callback_query(c.id,'اتصال ناموفق؛ جزئیات در لاگ سرویس.',show_alert=True)
        log.warning('Panel test failed for %s: %s',sid,exc)

@bot.callback_query_handler(func=lambda c:c.data.startswith('stoggle:'))
def server_toggle(c):
    if not admin(c.from_user.id):return
    sid=int(c.data.split(':')[1]);s=next((x for x in db.servers_admin() if x['id']==sid),None)
    if not s:return bot.answer_callback_query(c.id,'سرور پیدا نشد.')
    db.set_server_active(sid,not bool(s['active']));db.audit(c.from_user.id,'server.toggle',str(sid),str(not bool(s['active'])))
    bot.answer_callback_query(c.id,'وضعیت تغییر کرد.');admin_servers(c)

@bot.callback_query_handler(func=lambda c:c.data in {'add_product','add_server','credit','user_block','broadcast','admin_add','admin_remove'})
def admin_wizard(c):
    if not admin(c.from_user.id):return
    if c.data=='broadcast' and not feature('broadcast','0'):
        return bot.answer_callback_query(c.id,'پیام همگانی غیرفعال است.',show_alert=True)
    section = 'store' if c.data in {'add_product','add_server'} else 'users'
    prompts={'add_product':'نام پلن را ارسال کنید. مراحل بعد: قیمت، حجم GB، روز، توضیح، Group IDs و شناسه دسته‌بندی (۰ یعنی بدون دسته).','add_server':'نوع پنل را وارد کنید: pasarguard (پیشنهادی)، marzban یا marzneshin. سپس نام سرور، URL، نام کاربری و رمز API را وارد کنید.','credit':'شناسه کاربر را ارسال کنید؛ سپس مبلغ مثبت.','user_block':'شناسه عددی کاربر را وارد کنید تا وضعیت مسدودسازی تغییر کند.','broadcast':'متن پیام همگانی را ارسال کنید.','admin_add':'شناسه عددی مدیر جدید را ارسال کنید.','admin_remove':'شناسه عددی مدیر را ارسال کنید.'}
    enter_reply_mode(c, '<b>📝 ' + safe(c.data.replace('_', ' ')) + '</b>\n' + prompts[c.data], {'step':c.data}, return_to='admin_section:' + section)

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step') in {'add_product','add_server','credit','user_block','broadcast','admin_add','admin_remove','set_text','ticket_reply','product_price'})
def admin_input(m):
    if not admin(m.from_user.id):return
    st=states[m.from_user.id];step=st['step'];value=(m.text or '').strip()
    if step=='set_text':
        db.set_setting('text.'+st['key'],value);db.audit(m.from_user.id,'setting.text',st['key'],'updated');return finish_reply_mode(m.chat.id,m.from_user.id,'متن با موفقیت ذخیره شد.','admin_section:interface')
    if step=='broadcast':
        states.pop(m.from_user.id,None);sent=failed=0
        for uid in db.users_for_broadcast():
            try:bot.send_message(uid,safe(value));sent+=1
            except Exception:failed+=1
            time.sleep(.05)
        db.audit(m.from_user.id,'broadcast',None,f'sent={sent};failed={failed}');return finish_reply_mode(m.chat.id,m.from_user.id,f'ارسال همگانی پایان یافت.\nموفق: {sent} · ناموفق: {failed}','admin_section:users')
    if step in {'user_block','admin_add','admin_remove'}:
        try:uid=int(value)
        except ValueError:return bot.send_message(m.chat.id,'شناسه باید عددی باشد.')
        if step=='user_block':
            u=db.get_user(uid)
            if not u:return bot.send_message(m.chat.id,'کاربر پیدا نشد.')
            db.set_user_blocked(uid,not bool(u['is_blocked']));db.audit(m.from_user.id,'user.block',str(uid),str(not bool(u['is_blocked'])));return finish_reply_mode(m.chat.id,m.from_user.id,'وضعیت کاربر تغییر کرد.','admin_section:users')
        if step=='admin_add':db.add_admin(uid);db.audit(m.from_user.id,'admin.add',str(uid))
        else:
            if uid in settings.admin_ids:return bot.send_message(m.chat.id,'مدیر اصلی از طریق ADMIN_IDS حذف می‌شود.')
            db.remove_admin(uid);db.audit(m.from_user.id,'admin.remove',str(uid))
        return finish_reply_mode(m.chat.id,m.from_user.id,'دسترسی مدیر به‌روزرسانی شد.','admin_section:users')
    if step=='ticket_reply':
        tid=st['ticket_id'];ticket=db.reply_ticket(tid,m.from_user.id,value);bot.send_message(ticket['user_id'],f'پاسخ پشتیبانی به تیکت #{tid}:\n{safe(value)}');return finish_reply_mode(m.chat.id,m.from_user.id,'پاسخ تیکت ارسال شد.','admin_section:support')
    if step=='product_price':
        try:price=int(value.replace(',',''))
        except ValueError:return bot.send_message(m.chat.id,'قیمت باید عددی باشد.')
        if price<0:return bot.send_message(m.chat.id,'قیمت نمی‌تواند منفی باشد.')
        db.update_product_price(st['product_id'],price);db.audit(m.from_user.id,'product.price',str(st['product_id']),str(price));return finish_reply_mode(m.chat.id,m.from_user.id,'قیمت پلن به‌روزرسانی شد.','admin_section:store')
    vals=st.setdefault('values',[]);vals.append(value)
    required={'add_product':7,'add_server':5,'credit':2}[step]
    if len(vals)<required:
        labels={'add_product':['نام پلن','قیمت','حجم GB','مدت اعتبار به روز (0 نامحدود)','توضیحات','Group IDs به‌شکل JSON مثل [1,2]','شناسه دسته‌بندی (۰ بدون دسته)'],'add_server':['نوع پنل: pasarguard / marzban / marzneshin','نام سرور','URL پنل با https://','نام کاربری API','رمز API'],'credit':['شناسه کاربر','مبلغ افزایش موجودی']}
        bot.send_message(m.chat.id,f"<b>مرحله {len(vals)+1} از {required}</b>\n{labels[step][len(vals)]}");return
    try:
        if step=='add_product':
            name,price,gb,days,desc,groups,category=vals
            if not all(re.fullmatch(r'\d+',x) for x in [price,gb,days,category]):raise ValueError('قیمت، حجم، روز و شناسه دسته باید عدد نامنفی باشند.')
            if (int(gb)==0 or int(days)==0) and not feature('unlimited_plan','0'):
                raise ValueError('برای ساخت پلن با حجم یا زمان نامحدود، ابتدا قابلیت پلن نامحدود را فعال کنید.')
            group_ids=json.loads(groups or '[]')
            if not isinstance(group_ids,list) or any(type(x) is not int or x<0 for x in group_ids):raise ValueError('Group IDs باید آرایه‌ای از اعداد صحیح نامنفی باشد.')
            category_id=int(category) or None
            if category_id and not any(int(x['id'])==category_id for x in db.categories()):raise ValueError('شناسه دسته‌بندی وجود ندارد.')
            db.add_product(name,desc,int(price),int(gb),int(days),json.dumps(group_ids),category_id);db.audit(m.from_user.id,'product.add',name)
            return finish_reply_mode(m.chat.id,m.from_user.id,'پلن جدید با موفقیت ایجاد شد.','admin_section:store')
        elif step=='add_server':
            panel_type,name,url,user,pw=vals
            panel_type=panel_type.lower()
            if panel_type not in {'pasarguard','marzban','marzneshin'}:raise ValueError('نوع پنل پشتیبانی‌شده: pasarguard، marzban، marzneshin')
            if settings.verify_tls and not url.startswith('https://'):raise ValueError('برای TLS فعال، URL باید https باشد.')
            if not box:raise RuntimeError('FERNET_KEY تنظیم نشده است.')
            client_cls={'pasarguard':PasarGuardClient,'marzban':MarzbanClient,'marzneshin':MarzneshinClient}[panel_type]
            client_cls(url,user,pw,settings.verify_tls).login();db.add_server(name,url,user,box.encrypt(pw),panel_type);db.audit(m.from_user.id,'server.add',name,panel_type)
            return finish_reply_mode(m.chat.id,m.from_user.id,'اتصال پنل تأیید و سرور ذخیره شد.','admin_section:store')
        elif step=='credit':
            uid,amount=map(int,vals)
            if amount<=0:raise ValueError('مبلغ باید مثبت باشد.')
            db.add_credit(uid,amount,f'admin:{m.from_user.id}:{secrets.token_hex(8)}');db.audit(m.from_user.id,'wallet.credit',str(uid),str(amount))
            try:bot.send_message(uid,f'کیف پول شما به مبلغ {fmt(amount)} توسط مدیریت شارژ شد.')
            except Exception:pass
            return finish_reply_mode(m.chat.id,m.from_user.id,'کیف پول کاربر شارژ شد.','admin_section:users')
    except Exception as exc:
        # Keep the Reply keyboard workflow alive and let the admin retry the last field.
        if m.from_user.id in states and states[m.from_user.id].get('values'):
            states[m.from_user.id]['values'] = states[m.from_user.id]['values'][:-1]
        bot.send_message(m.chat.id,'خطا: '+safe(exc)+'\nلطفاً مقدار مرحله آخر را دوباره ارسال کنید.')

@bot.callback_query_handler(func=lambda c:c.data=='admin_deposits')
def admin_deposits(c):
    if not admin(c.from_user.id):return
    ensure_inline_mode(c.message.chat.id, c.from_user.id)
    rows=db.pending_deposits()
    if not rows:
        kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به مالی',callback_data='admin_section:finance'))
        return edit_or_send(c.message.chat.id,'<b>🧾 رسیدهای معلق</b>\nدر حال حاضر رسیدی برای بررسی وجود ندارد.',kb,c.message.message_id)
    clear_inline(c)
    edit_or_send(c.message.chat.id, f'<b>🧾 رسیدهای در انتظار بررسی</b>\nتعداد رسیدهای معلق: <b>{len(rows)}</b>\nبرای هر رسید، تأیید یا رد را انتخاب کنید.', None, c.message.message_id)
    for d in rows:
        k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🔎 بررسی رسید',callback_data=f"dep_review:{d['id']}"))
        try:
            sent = bot.send_photo(c.message.chat.id,d['receipt_id'],caption=f"رسید #{d['id']} | کاربر {d['user_id']} | {fmt(d['amount'])}",reply_markup=k)
        except Exception:
            sent = bot.send_message(c.message.chat.id,f"رسید #{d['id']} | کاربر {d['user_id']} | {fmt(d['amount'])}",reply_markup=k)
        register_inline_message(c.message.chat.id, getattr(sent, 'message_id', None))
    back=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به مالی و پرداخت',callback_data='admin_section:finance'))
    sent = bot.send_message(c.message.chat.id,'برای خروج از فهرست رسیدها:',reply_markup=back)
    register_inline_message(c.message.chat.id, getattr(sent, 'message_id', None))

@bot.callback_query_handler(func=lambda c: c.data == 'admin_users')
def admin_users(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    rows = db.list_users(8)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for user in rows:
        status = '🔴' if user['is_blocked'] else '🟢'
        kb.add(types.InlineKeyboardButton(f"{status} {user['id']} · @{user['username'] or '-'} · {fmt(user['balance'])}", callback_data=f"user_detail:{user['id']}"))
    kb.add(types.InlineKeyboardButton('🔎 جستجوی دقیق کاربر', callback_data='user_search'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به کاربران و دسترسی‌ها', callback_data='admin_section:users'))
    text = '<b>👥 کاربران اخیر</b>\nبرای مشاهده حساب و عملیات مدیریتی، یک کاربر را انتخاب کنید. برای فهرست کامل‌تر از جستجو استفاده کنید.' if rows else '<b>👥 کاربران</b>\nهنوز کاربری ثبت نشده است.'
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c:c.data=='admin_tickets')
def admin_tickets(c):
    if not admin(c.from_user.id):return
    rows=db.open_tickets();k=types.InlineKeyboardMarkup()
    for t in rows:k.add(types.InlineKeyboardButton(f"#{t['id']} — {t['subject'][:34]}",callback_data=f"ticket:{t['id']}"))
    k.add(types.InlineKeyboardButton('⬅️ بازگشت به پشتیبانی',callback_data='admin_section:support'))
    edit_or_send(c.message.chat.id,'<b>🎫 تیکت‌های باز</b>\nیک تیکت را برای مشاهده و پاسخ انتخاب کنید.' if rows else '<b>🎫 تیکت‌های باز</b>\nتیکت بازی وجود ندارد.',k,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('ticket:'))
def ticket_open(c):
    if not admin(c.from_user.id):return
    tid=int(c.data.split(':')[1]);msgs=db.ticket_messages(tid);body='\n'.join(f"{x['sender_id']}: {x['body']}" for x in msgs)[-3000:]
    clear_inline(c)
    prompt_reply_mode(c.message.chat.id,c.from_user.id,f'<b>🎫 تیکت #{tid}</b>\n{safe(body)}\n\nبرای پاسخ، متن را ارسال کنید؛ برای بستن تیکت از دکمه مخصوص استفاده کنید.',{'step':'ticket_reply','ticket_id':tid},rows=[(REPLY_BACK,REPLY_CANCEL),(REPLY_CLOSE_TICKET,)],return_to='admin_section:support')

@bot.callback_query_handler(func=lambda c:c.data.startswith('ticketclose:'))
def ticket_close(c):
    if not admin(c.from_user.id):return
    tid=int(c.data.split(':')[1]);db.close_ticket(tid);db.audit(c.from_user.id,'ticket.close',str(tid));bot.answer_callback_query(c.id,'تیکت بسته شد.')
    try:
        bot.edit_message_reply_markup(c.message.chat.id,c.message.message_id,reply_markup=None)
        inline_message_ids.get(c.message.chat.id, set()).discard(c.message.message_id)
    except Exception:pass

@bot.callback_query_handler(func=lambda c:c.data=='admin_admins')
def admin_admins(c):
    if not admin(c.from_user.id):return
    rows=db.list_admins();txt='\n'.join(f"{x['user_id']} — {x['role']} — {'فعال' if x['active'] else 'غیرفعال'}" for x in rows) or 'مدیر دیگری ثبت نشده است.'
    k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('➕ افزودن مدیر',callback_data='admin_add'),types.InlineKeyboardButton('➖ حذف مدیر',callback_data='admin_remove'))
    k.add(types.InlineKeyboardButton('⬅️ بازگشت به کاربران',callback_data='admin_section:users'))
    edit_or_send(c.message.chat.id,'<b>🛡 مدیران ربات</b>\n'+safe(txt),k,c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_reviews')
def admin_reviews(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    rows = db.review_orders()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for order in rows[:20]:
        kb.add(types.InlineKeyboardButton(f"⚠️ {order['id'][:8]} · کاربر {order['user_id']} · {fmt(order['amount'])}", callback_data=f"review_detail:{order['id']}"))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به سیستم و امنیت', callback_data='admin_section:system'))
    text = '<b>⚠️ سفارش‌های نیازمند بررسی</b>\nیک سفارش را باز کنید تا جزئیات آن را ببینید.\n\n' + (f'تعداد موارد: <b>{len(rows)}</b>' if rows else 'موردی برای بررسی وجود ندارد.')
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('review_detail:'))
def review_detail(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    oid = c.data.split(':', 1)[1]
    order = next((item for item in db.review_orders() if item['id'] == oid), None)
    if not order:
        return bot.answer_callback_query(c.id, 'این سفارش دیگر در صف بررسی نیست.', show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=1)
    if order.get('server_id') and order.get('remote_username'):
        kb.add(types.InlineKeyboardButton('🔄 بررسی اکانت موجود در پنل و تکمیل سفارش', callback_data=f'reviewreconcile:{oid}'))
    kb.add(types.InlineKeyboardButton('💸 ادامه به بازپرداخت', callback_data=f'reviewrefund_prepare:{oid}'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به فهرست بررسی', callback_data='admin_reviews'))
    text = (
        f"<b>⚠️ سفارش {oid[:12]}</b>\n"
        f"کاربر: <code>{order['user_id']}</code>\n"
        f"مبلغ: <b>{fmt(order['amount'])}</b>\n"
        f"نام کاربری پنل: <code>{safe(order['remote_username'] or '-')}</code>\n\n"
        f"<b>جزئیات خطا</b>\n{safe(order['error'] or 'بدون جزئیات ثبت‌شده')}\n\n"
        'قبل از بازپرداخت، وضعیت اکانت را در پنل مقصد بررسی کنید.'
    )
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('reviewreconcile:'))
def review_reconcile(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    oid = c.data.split(':', 1)[1]
    order = next((item for item in db.review_orders() if item['id'] == oid), None)
    if not order or not order.get('server_id') or not order.get('remote_username'):
        return bot.answer_callback_query(c.id, 'اطلاعات سرور یا نام کاربری پنل برای تطبیق موجود نیست.', show_alert=True)
    server = next((row for row in db.servers_admin() if int(row['id']) == int(order['server_id'])), None)
    if not server:
        return bot.answer_callback_query(c.id, 'سرور مرتبط پیدا نشد؛ ابتدا سرور را بررسی کنید.', show_alert=True)
    bot.answer_callback_query(c.id, 'در حال بررسی وضعیت اکانت در پنل…')
    try:
        client = server_client(server)
        remote = client.get_user(order['remote_username'])
        sub = client.subscription_url(remote, order['remote_username']) if hasattr(client, 'subscription_url') else client._sub(remote)
        if not sub:
            raise RuntimeError('پنل اکانت را برگرداند اما لینک اشتراک قابل استخراج نبود.')
        limit = int(remote.get('data_limit') or 0) if remote.get('data_limit') is not None else None
        expiry_keys = ('expire', 'expire_date', 'expires_at', 'expiryTime')
        expiry_entries = [(key, remote[key]) for key in expiry_keys if key in remote]
        if not expiry_entries:
            expiry = UNSET
        else:
            selected = next(((key, value) for key, value in expiry_entries
                             if value not in (None, '', 0, '0')), None)
            if selected is None:
                expiry = None  # The panel explicitly reports an unlimited/cleared expiry.
            else:
                expiry_key, expire_value = selected
                if expiry_key == 'expiryTime':
                    expire_value = float(expire_value) / 1000
                expiry_dt = client._as_datetime(expire_value) if hasattr(client, '_as_datetime') else None
                if expiry_dt is None:
                    raise RuntimeError('پنل مقدار انقضای قابل‌خواندن برنگرداند؛ تکمیل سفارش نیازمند بررسی دستی است.')
                expiry = expiry_dt.isoformat()
        cashback_pct = int(db.get_setting('cashback.purchase_percent','0') or 0) if feature('cashback','0') else 0
        referral_pct = int(db.get_setting('referral.purchase_percent','0') or 0) if feature('reseller_commission','0') else -1
        if not db.complete_review_order(oid, sub, data_limit_bytes=limit, expires_at=expiry,
                                        cashback_percent=cashback_pct, referral_percent=referral_pct):
            raise RuntimeError('ثبت نهایی سفارش در دیتابیس انجام نشد؛ وضعیت برای بررسی حفظ شد.')
        db.audit(c.from_user.id, 'order.review_reconciled', oid, f"server={server['id']};username={order['remote_username']}")
        try:
            bot.send_message(order['user_id'], f'✅ سفارش بررسی‌شده شما تکمیل شد. لینک اشتراک:\n<code>{safe(sub)}</code>')
        except Exception:
            pass
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('بازگشت به سفارش‌های بررسی', callback_data='admin_reviews'))
        edit_or_send(c.message.chat.id, f'✅ اکانت موجود تأیید و سفارش <code>{oid[:12]}</code> بدون ساخت مجدد اکانت تکمیل شد.', kb, c.message.message_id)
    except Exception as exc:
        db.audit(c.from_user.id, 'order.review_reconcile_failed', oid, str(exc)[:500])
        log.exception('Review order reconciliation failed')
        bot.send_message(c.message.chat.id, 'تطبیق خودکار موفق نبود؛ سفارش در صف بررسی باقی ماند.\n' + safe(exc))


@bot.callback_query_handler(func=lambda c: c.data.startswith('reviewrefund_prepare:'))
def review_refund_prepare(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    oid = c.data.split(':', 1)[1]
    order = next((item for item in db.review_orders() if item['id'] == oid), None)
    if not order:
        return bot.answer_callback_query(c.id, 'این سفارش دیگر در صف بررسی نیست.', show_alert=True)
    text = (
        '<b>💸 تأیید بازپرداخت</b>\n'
        f"شناسه سفارش: <code>{oid[:12]}</code>\n"
        f"کاربر: <code>{order['user_id']}</code>\n"
        f"مبلغ: <b>{fmt(order['amount'])}</b>\n\n"
        'فقط پس از بررسی پنل و اطمینان از وضعیت اکانت، بازپرداخت را تأیید کنید.'
    )
    enter_reply_mode(c, text, {'step':'confirm_review_refund','order_id':oid},
                     rows=[(REPLY_CONFIRM_REFUND,), (REPLY_BACK, REPLY_CANCEL)],
                     return_to='admin_section:system')


def process_review_refund(chat_id, admin_id, oid):
    order = db.refund_review_order(oid)
    if not order:
        kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به سیستم و امنیت', callback_data='admin_section:system'))
        return edit_or_send(chat_id, 'این سفارش قبلاً تعیین تکلیف شده است.', kb)
    db.audit(admin_id, 'order.review_refund', oid)
    try:
        bot.send_message(order['user_id'], f'مبلغ سفارش بررسی‌شده ({fmt(order["amount"])}) به کیف پول برگشت داده شد.')
    except Exception:
        pass
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('مشاهده سفارش‌های بررسی', callback_data='admin_reviews'))
    return edit_or_send(chat_id, f'✅ بازپرداخت سفارش <code>{oid[:12]}</code> انجام شد.', kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith('reviewrefund:'))
def review_refund(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    oid = c.data.split(':', 1)[1]
    bot.answer_callback_query(c.id, 'در حال بررسی درخواست بازپرداخت…')
    return process_review_refund(c.message.chat.id, c.from_user.id, oid)


@bot.message_handler(commands=['admin'])
def admin_command(m):
    if admin(m.from_user.id):
        remove_reply_keyboard(m.chat.id, 'ورود به مرکز مدیریت')
        admin_panel(m.chat.id, uid=m.from_user.id)


# ---------------- SonarX 6.1: focused UI and exclusive keyboard modes ----------------
FEATURE_GROUPS = [
 ("فروش و سرویس", [
  ("shop_enabled","فروش سرویس"),("new_member","عضویت کاربر جدید"),("bulk_create","ساخت گروهی اکانت"),
  ("single_buy","خرید تکی"),("test_account","اکانت تست"),("test_one_per_server","یک تست از هر سرور"),
  ("renew_service","تمدید سرویس"),("bulk_renew","تمدید تجمیعی"),("increase_volume","افزایش حجم"),
  ("increase_time","افزایش زمان"),("custom_plan","پلن دلخواه"),("unlimited_plan","پلن نامحدود"),
  ("service_status","وضعیت سرویس"),("service_delete","حذف کانفیگ"),("auto_renew","تمدید خودکار")
 ]),
 ("مالی و تخفیف", [
  ("deposits_enabled","شارژ کیف پول"),("payment_gateway","درگاه پرداخت"),("manual_receipt","پرداخت کارت‌به‌کارت"),
  ("discounts","کد تخفیف"),("first_purchase_discount","تخفیف خرید اول"),("gift_codes","کد هدیه"),
  ("usd_currency","ارز دلاری"),("wallet_hold","موجودی معلق"),("cashback","کش‌بک و کمیسیون"),("reseller","سیستم نمایندگی"),
  ("reseller_commission","کمیسیون نماینده"),("referral","دعوت دوستان"),("lottery","قرعه‌کشی")
 ]),
 ("کاربر و ارتباطات", [
  ("support_enabled","تیکت و پشتیبانی"),("broadcast","پیام همگانی"),("faq","سوالات متداول"),
  ("tutorials","آموزش اتصال"),("rules","قوانین ربات"),("welcome_report","گزارش خوش‌آمد"),
  ("phone_verify","تأیید شماره"),("iran_phone_verify","تأیید شماره ایرانی"),("user_search","جستجوی کاربران"),
  ("account_info","مشخصات کاربری"),("channel_membership","عضویت اجباری کانال")
 ]),
 ("فنی و نمایش", [
  ("miniapp","مینی‌اپ"),("webapp_link","لینک وب‌اپ"),("web_panel","پنل وب"),("qr_code","QR Code"),
  ("protocol_change","تغییر پروتکل"),("location_change","تغییر لوکیشن"),("xui_sub_link","ساب لینک XUI"),
  ("sub_link","لینک اشتراک"),("subscription_link","لینک ولوم وب"),("config_copy","کپی کانفیگ"),
  ("traffic_sync","همگام‌سازی مصرف"),("server_health","گزارش سلامت سرورها")
 ]),
 ("مدیریت و امنیت", [
  ("maintenance","حالت تعمیرات"),("admin_roles","سطوح دسترسی مدیران"),("audit_log","ثبت لاگ مدیریتی"),
  ("backup","پشتیبان‌گیری"),("rate_limit","محدودیت درخواست"),("anti_fraud","ضدتقلب"),
  ("auto_delete_expired","حذف خودکار منقضی"),("auto_cleanup","پاکسازی خودکار"),("reseller_panel","پنل نماینده")
 ])
]

IMPLEMENTED_FEATURES={'shop_enabled','deposits_enabled','support_enabled','manual_receipt','discounts','gift_codes','faq','tutorials','audit_log','service_status','qr_code','account_info','user_search','sub_link'}
OPERATIONAL_FEATURES={
    'shop_enabled','deposits_enabled','support_enabled','manual_receipt','discounts','gift_codes',
    'faq','tutorials','audit_log','service_status','qr_code','referral','dice_game','lottery',
    'test_account','custom_plan','cashback','reseller','reseller_panel','reseller_commission',
    'phone_verify','iran_phone_verify','channel_membership','renew_service','increase_volume',
    'unlimited_plan','broadcast','user_search','account_info','maintenance','sub_link'
}
def feature_default(key):
    if key in {'test_account','custom_plan','cashback','phone_verify','channel_membership'}:
        return '0'
    return '1' if key in IMPLEMENTED_FEATURES else '0'

def _feature_page(chat_id, page=0, message_id=None):
    page = max(0, min(int(page), len(FEATURE_GROUPS) - 1))
    group, items = FEATURE_GROUPS[page]
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for key, label in items:
        state = db.get_setting('feature.' + key, feature_default(key))
        icon = ('🟢' if state == '1' else '⚪') if key in OPERATIONAL_FEATURES else '🧩'
        buttons.append(types.InlineKeyboardButton(f'{icon} {label}', callback_data=f'fx:{key}:{page}'))
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i+2])
    nav = []
    if page:
        nav.append(types.InlineKeyboardButton('◀ قبلی', callback_data=f'feature_matrix:{page-1}'))
    if page < len(FEATURE_GROUPS) - 1:
        nav.append(types.InlineKeyboardButton('بعدی ▶', callback_data=f'feature_matrix:{page+1}'))
    if nav:
        kb.row(*nav)
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به ظاهر و تجربه کاربری', callback_data='admin_section:interface'))
    kb.add(types.InlineKeyboardButton('🏠 مرکز مدیریت', callback_data='admin'))
    enabled = sum(1 for key, _ in items if key in OPERATIONAL_FEATURES and db.get_setting('feature.' + key, feature_default(key)) == '1')
    text = (
        '<b>🎚 ماتریس قابلیت‌های SonarX</b>\n'
        f'<i>{safe(group)}</i>  ·  بخش {page+1} از {len(FEATURE_GROUPS)}\n\n'
        f'فعال در این گروه: <b>{enabled} از {len(items)}</b>\n'
        '🟢 فعال · ⚪ غیرفعال · 🧩 ماژول اجرایی هنوز تکمیل نشده است.\n'
        'قابلیت‌های دارای علامت 🧩 در این نسخه عمداً قابل فعال‌سازی نیستند.'
    )
    return edit_or_send(chat_id, text, kb, message_id)


@bot.callback_query_handler(func=lambda c:c.data=='trial_settings')
def trial_settings_start(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    current=(db.get_setting('trial.gb','1'),db.get_setting('trial.days','1'),
             db.get_setting('trial.max_per_user','1'),db.get_setting('trial.group_ids','[]'))
    enter_reply_mode(c,
        '<b>🆓 تنظیم اکانت تست</b>\n'
        f'تنظیم فعلی: {current[0]} GB، {current[1]} روز، حداکثر {current[2]} بار برای هر کاربر\n'
        'مقادیر جدید را به شکل زیر ارسال کنید:\n<code>GB,روز,سهمیه هر کاربر,GroupIDs</code>\n'
        'مثال: <code>1,1,1,[1]</code>',
        {'step':'trial_settings'},return_to='admin_section:store')

@bot.callback_query_handler(func=lambda c:c.data=='custom_plan_settings')
def custom_plan_settings_start(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    gb=db.get_setting('custom.price_per_gb','0');day=db.get_setting('custom.price_per_day','0')
    groups=db.get_setting('custom.group_ids','[]')
    enter_reply_mode(c,
        '<b>🧩 تنظیم پلن دلخواه</b>\n'
        f'قیمت فعلی هر GB: {fmt(gb)}\nقیمت هر روز: {fmt(day)}\nگروه‌ها: <code>{safe(groups)}</code>\n'
        'تنظیم جدید را به شکل <code>قیمت هر GB,قیمت هر روز,GroupIDs</code> ارسال کنید.',
        {'step':'custom_plan_settings'},return_to='admin_section:store')

@bot.callback_query_handler(func=lambda c:c.data=='cashback_settings')
def cashback_settings_start(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    vals=[db.get_setting('cashback.purchase_percent','0'),db.get_setting('cashback.deposit_percent','0'),db.get_setting('referral.purchase_percent','0')]
    enter_reply_mode(c,
        '<b>🎁 تنظیم کش‌بک و کمیسیون</b>\n'
        f'خرید: {vals[0]}٪ · شارژ: {vals[1]}٪ · کمیسیون دعوت: {vals[2]}٪\n'
        'مقادیر جدید را به شکل <code>درصد خرید,درصد شارژ,درصد دعوت</code> ارسال کنید. هر مقدار بین ۰ تا ۱۰۰.',
        {'step':'cashback_settings'},return_to='admin_section:finance')

@bot.callback_query_handler(func=lambda c:c.data=='wheel_settings')
def wheel_settings_start(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    vals=[db.get_setting('wheel.reward',db.get_setting('dice_reward','0')),
          db.get_setting('wheel.cooldown_hours','24'),db.get_setting('wheel.mode','dice'),
          db.get_setting('wheel.first_purchase_only','0')]
    enter_reply_mode(c,
        '<b>🎰 تنظیم چرخ شانس</b>\n'
        f'جایزه: {vals[0]} · فاصله: {vals[1]} ساعت · حالت: {vals[2]} · فقط خرید اول: {vals[3]}\n'
        'مقادیر جدید: <code>جایزه,ساعت انتظار,dice|slot,فقط خریدار اول 0|1</code>\n'
        'مثال: <code>50000,24,dice,0</code>',
        {'step':'wheel_settings'},return_to='admin_section:growth')

@bot.callback_query_handler(func=lambda c:c.data.startswith('feature_matrix:'))
def feature_matrix(c):
    if not admin(c.from_user.id): return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    _feature_page(c.message.chat.id,int(c.data.split(':')[1]),c.message.message_id)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('fx:'))
def feature_toggle(c):
    if not admin(c.from_user.id): return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    _,key,page=c.data.split(':')
    if key not in OPERATIONAL_FEATURES:
        return bot.answer_callback_query(c.id,'ماژول اجرایی این قابلیت هنوز تکمیل نشده؛ برای جلوگیری از فعال‌سازی صوری، وضعیت آن تغییر نکرد.',show_alert=True)
    old=db.get_setting('feature.'+key,feature_default(key))
    new='0' if old=='1' else '1'
    db.set_setting('feature.'+key,new);db.audit(c.from_user.id,'feature.toggle',key,new)
    bot.answer_callback_query(c.id,'تنظیم ذخیره شد.')
    _feature_page(c.message.chat.id,int(page),c.message.message_id)

@bot.message_handler(commands=['panel'])
def admin_shortcut(m):
    if admin(m.from_user.id):
        remove_reply_keyboard(m.chat.id, 'ورود به مرکز مدیریت')
        admin_panel(m.chat.id, uid=m.from_user.id)
    else: bot.reply_to(m,'دسترسی ندارید.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='gift_redeem')
def redeem_gift_message(m):
    if not feature('gift_codes','1'):
        return finish_reply_mode(m.chat.id,m.from_user.id,'کد هدیه غیرفعال است.')
    code=(m.text or '').strip().upper()
    try:
        g=db.redeem_gift(code,m.from_user.id)
        if not g: return bot.send_message(m.chat.id,'کد نامعتبر، غیرفعال یا قبلاً مصرف شده است.')
        finish_reply_mode(m.chat.id,m.from_user.id,f"کد هدیه با موفقیت ثبت شد.\nاعتبار افزوده‌شده: {fmt(g['amount'])}")
    except Exception as e: bot.send_message(m.chat.id,'خطا در ثبت کد: '+safe(e))

@bot.callback_query_handler(func=lambda c:c.data=='redeem_gift')
def redeem_gift_start(c):
    if not feature('gift_codes','1'):return bot.answer_callback_query(c.id,'کد هدیه غیرفعال است.',show_alert=True)
    enter_reply_mode(c,'<b>🎁 ثبت کد هدیه</b>\nکد هدیه خود را ارسال کنید.',{'step':'gift_redeem'})

@bot.callback_query_handler(func=lambda c:c.data=='tutorials')
def tutorials(c):
    txt=text_setting('tutorials','آموزش اتصال: لینک اشتراک را در برنامه سازگار با پروتکل سرویس وارد کنید.')
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به مرکز راهنما',callback_data='help_center'))
    edit_or_send(c.message.chat.id,'<b>📚 آموزش اتصال</b>\n\n'+safe(txt),kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='faq')
def faq(c):
    txt=text_setting('faq','سوالات متداول:\n۱) لینک اشتراک را در کلاینت وارد کنید.\n۲) در صورت اختلال تیکت ثبت کنید.\n۳) تمدید از بخش مدیریت/پشتیبانی پیگیری می‌شود.')
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به مرکز راهنما',callback_data='help_center'))
    edit_or_send(c.message.chat.id,'<b>❓ سوالات متداول</b>\n\n'+safe(txt),kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == 'user_home')
def user_home(c):
    edit_or_send(c.message.chat.id, home_text(c.from_user.id), home(c.from_user.id), c.message.message_id)
    bot.answer_callback_query(c.id)


@bot.callback_query_handler(func=lambda c: c.data == 'help_center')
def help_center(c):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(types.InlineKeyboardButton('📚 آموزش اتصال', callback_data='tutorials'), types.InlineKeyboardButton('❓ سوالات متداول', callback_data='faq'))
    if feature('support_enabled', '1'):
        kb.add(types.InlineKeyboardButton('🎫 ارتباط با پشتیبانی', callback_data='support'))
    kb.add(types.InlineKeyboardButton('🏠 منوی اصلی', callback_data='user_home'))
    edit_or_send(c.message.chat.id, '<b>📚 مرکز راهنما</b>\nآموزش‌ها، پاسخ پرسش‌های رایج و راه ارتباط با پشتیبانی را از این بخش دنبال کنید.', kb, c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='reseller_request')
def reseller_request_start(c):
    if not feature('reseller','0'):
        return bot.answer_callback_query(c.id,'درخواست نمایندگی فعلاً غیرفعال است.',show_alert=True)
    if db.get_reseller(c.from_user.id):
        return bot.answer_callback_query(c.id,'شما هم‌اکنون نماینده فعال هستید.')
    enter_reply_mode(c,'<b>🤝 درخواست نمایندگی</b>\nتوضیح کوتاهی درباره فعالیت و حجم فروش موردانتظار خود ارسال کنید.',{'step':'reseller_request'},return_to='user_home')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='reseller_request')
def reseller_request_message(m):
    description=(m.text or '').strip()
    if len(description)<10:return bot.send_message(m.chat.id,'توضیح درخواست کوتاه است؛ حداقل ۱۰ نویسه ارسال کنید.')
    rid=db.create_reseller_request(m.from_user.id,description)
    if not rid:return finish_reply_mode(m.chat.id,m.from_user.id,'یک درخواست نمایندگی در انتظار بررسی دارید.','user_home')
    db.audit(m.from_user.id,'reseller.request',str(rid),'submitted')
    for aid in settings.admin_ids:
        try:
            kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🔎 بررسی درخواست',callback_data=f'reseller_request_review:{rid}'))
            bot.send_message(aid,f'📨 <b>درخواست نمایندگی جدید</b>\nکاربر: <code>{m.from_user.id}</code>\n{safe(description)}',reply_markup=kb)
        except Exception:pass
    return finish_reply_mode(m.chat.id,m.from_user.id,'درخواست نمایندگی برای مدیریت ارسال شد. پس از بررسی نتیجه اعلام می‌شود.','user_home')

@bot.callback_query_handler(func=lambda c:c.data=='reseller_panel')
def reseller_panel(c):
    if not feature('reseller_panel','0'):
        return bot.answer_callback_query(c.id,'پنل نمایندگی غیرفعال است.',show_alert=True)
    reseller=db.get_reseller(c.from_user.id)
    if not reseller:return bot.answer_callback_query(c.id,'پنل نمایندگی برای حساب شما فعال نیست.',show_alert=True)
    stats=db.reseller_stats(c.from_user.id)
    me=bot.get_me();link=f"https://t.me/{me.username}?start={c.from_user.id}"
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.row(types.InlineKeyboardButton('🔗 لینک دعوت',url=f"https://t.me/share/url?url={link}"),
           types.InlineKeyboardButton('🏠 منوی اصلی',callback_data='user_home'))
    text=(f"👨‍💼 <b>پنل نمایندگی SonarX</b>\n━━━━━━━━━━━━━━\n"
          f"درصد کمیسیون: <b>{reseller['commission_percent']}٪</b>\n"
          f"دعوت‌های موفق: <b>{stats['referrals']}</b>\n"
          f"فروش زیرمجموعه‌ها: <b>{fmt(stats['sales'])}</b>\n"
          f"کمیسیون ثبت‌شده: <b>{fmt(stats['commission'])}</b>\n\n"
          f"لینک اختصاصی شما:\n<code>{safe(link)}</code>")
    edit_or_send(c.message.chat.id,text,kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='referral')
def referral(c):
    if not feature('referral','0'):return bot.answer_callback_query(c.id,'دعوت دوستان غیرفعال است.',show_alert=True)
    me=bot.get_me();stats=db.referral_stats(c.from_user.id)
    link=f"https://t.me/{me.username}?start={c.from_user.id}"
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🏠 منوی اصلی',callback_data='user_home'))
    edit_or_send(c.message.chat.id,f"<b>🤝 دعوت دوستان</b>\nلینک اختصاصی شما:\n<code>{safe(link)}</code>\nتعداد دعوت موفق: {stats['n']}\nپاداش عضویت: {fmt(stats['bonus'])}\nکمیسیون خرید دعوت‌شده‌ها: {fmt(stats.get('commission',0))}",kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c: c.data == 'dice_game')
def dice_game(c):
    if not feature('dice_game','0'):
        return bot.answer_callback_query(c.id,'چرخ شانس فعلاً غیرفعال است.',show_alert=True)
    if db.get_setting('wheel.first_purchase_only','0')=='1' and db.has_paid_order(c.from_user.id):
        return bot.answer_callback_query(c.id,'این جایزه فقط برای کاربران بدون خرید قبلی فعال است.',show_alert=True)
    hours=int(db.get_setting('wheel.cooldown_hours','24') or 24)
    spin_id=db.claim_wheel_spin(c.from_user.id,hours)
    if not spin_id:
        return bot.answer_callback_query(c.id,f'هر کاربر فقط هر {hours} ساعت یک‌بار می‌تواند شرکت کند.',show_alert=True)
    mode=db.get_setting('wheel.mode','dice')
    emoji='🎰' if mode=='slot' else '🎲'
    try:
        roll=bot.send_dice(c.message.chat.id,emoji=emoji)
        value=int(roll.dice.value)
    except Exception as exc:
        db.finish_wheel_spin(spin_id,-1,0)
        log.exception('Wheel roll failed')
        return bot.answer_callback_query(c.id,'اجرای چرخ شانس ناموفق بود؛ بعداً دوباره تلاش کنید.',show_alert=True)
    reward=int(db.get_setting('wheel.reward',db.get_setting('dice_reward','0')) or 0)
    won=(value in {1,43,64,22}) if mode=='slot' else value==6
    paid=reward if won else 0
    db.finish_wheel_spin(spin_id,value,paid)
    if won and paid>0:
        text=f'🎉 <b>برنده شدید!</b>\n{fmt(paid)} به کیف پول شما اضافه شد.\nدفعه بعد پس از {hours} ساعت می‌توانید دوباره شرکت کنید.'
    elif won:
        text='نتیجه برنده است، اما مبلغ جایزه هنوز توسط مدیریت تنظیم نشده است.'
    else:
        text=f'این بار برنده نشدید. شانس بعدی شما {hours} ساعت دیگر است.'
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🏠 منوی اصلی',callback_data='user_home'))
    edit_or_send(c.message.chat.id,text,kb,c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == 'my_tickets')
def my_tickets(c):
    rows = db.tickets_for(c.from_user.id)
    if not rows:
        kb = types.InlineKeyboardMarkup(row_width=2)
        if feature('support_enabled', '1'):
            kb.add(types.InlineKeyboardButton('➕ تیکت جدید', callback_data='support'))
        kb.add(types.InlineKeyboardButton('🏠 منوی اصلی', callback_data='user_home'))
        return edit_or_send(c.message.chat.id, '<b>🎫 تیکت‌های شما</b>\nهنوز تیکتی ثبت نکرده‌اید.', kb, c.message.message_id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ticket in rows[:15]:
        status = '🟢 باز' if ticket['status'] == 'open' else '⚪ بسته'
        kb.add(types.InlineKeyboardButton(f"#{ticket['id']} · {ticket['subject'][:30]} · {status}", callback_data=f"my_ticket:{ticket['id']}"))
    kb.add(types.InlineKeyboardButton('➕ تیکت جدید', callback_data='support'))
    kb.add(types.InlineKeyboardButton('🏠 منوی اصلی', callback_data='user_home'))
    edit_or_send(c.message.chat.id, f'<b>🎫 تیکت‌های شما</b>\nتعداد درخواست‌ها: <b>{len(rows)}</b>\nبرای مشاهده گفتگو، تیکت را انتخاب کنید.', kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('my_ticket:'))
def my_ticket_detail(c):
    tid = int(c.data.split(':', 1)[1])
    ticket = next((item for item in db.tickets_for(c.from_user.id) if item['id'] == tid), None)
    if not ticket:
        return bot.answer_callback_query(c.id, 'تیکت پیدا نشد.', show_alert=True)
    messages = db.ticket_messages(tid)
    body = '\n\n'.join(f"{'شما' if item['sender_id'] == c.from_user.id else 'پشتیبانی'}: {item['body']}" for item in messages)
    if len(body) > 2800:
        body = '…\n' + body[-2800:]
    status = '🟢 باز' if ticket['status'] == 'open' else '⚪ بسته'
    kb = types.InlineKeyboardMarkup(row_width=2)
    if ticket['status'] == 'open':
        kb.add(types.InlineKeyboardButton('✍️ ارسال پیام جدید', callback_data=f'my_ticket_reply:{tid}'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به تیکت‌ها', callback_data='my_tickets'))
    edit_or_send(c.message.chat.id, f"<b>🎫 تیکت #{tid}</b> · {status}\nموضوع: <b>{safe(ticket['subject'])}</b>\n\n{safe(body) if body else 'هنوز پیامی ثبت نشده است.'}", kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('my_ticket_reply:'))
def my_ticket_reply_start(c):
    tid = int(c.data.split(':', 1)[1])
    ticket = next((item for item in db.tickets_for(c.from_user.id) if item['id'] == tid and item['status'] == 'open'), None)
    if not ticket:
        return bot.answer_callback_query(c.id, 'این تیکت باز نیست.', show_alert=True)
    enter_reply_mode(c, f'<b>✍️ ادامه تیکت #{tid}</b>\nپیام خود را ارسال کنید تا به همان درخواست اضافه شود.', {'step':'user_ticket_reply','ticket_id':tid})


@bot.callback_query_handler(func=lambda c: c.data == 'lottery_join')
def lottery_join(c):
    if not feature('lottery', '0'):
        return bot.answer_callback_query(c.id, 'قرعه‌کشی فعلاً غیرفعال است.', show_alert=True)
    try:
        db.join_lottery(c.from_user.id)
        bot.answer_callback_query(c.id, 'ثبت‌نام انجام شد.')
        text = '<b>🎟 ثبت‌نام شما در قرعه‌کشی انجام شد.</b>\nدر صورت انتخاب، از همین ربات اطلاع‌رسانی می‌شود.'
    except Exception:
        bot.answer_callback_query(c.id, 'قبلاً ثبت‌نام کرده‌اید یا خطایی رخ داده است.', show_alert=True)
        text = '<b>🎟 وضعیت قرعه‌کشی</b>\nشما قبلاً ثبت‌نام کرده‌اید یا امکان ثبت‌نام وجود ندارد.'
    edit_or_send(c.message.chat.id, text, home(c.from_user.id), c.message.message_id)


@bot.callback_query_handler(func=lambda c:c.data=='lottery_admin')
def lottery_admin(c):
    if not admin(c.from_user.id):return
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🎲 انتخاب برنده',callback_data='lottery_draw'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به رشد و بازاریابی',callback_data='admin_section:growth'))
    edit_or_send(c.message.chat.id,f"<b>🎲 قرعه‌کشی</b>\nشرکت‌کنندگان ثبت‌شده: <b>{db.lottery_count()}</b>",kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='addon_prices')
def addon_prices_start(c):
    if not admin(c.from_user.id):return
    gb=db.get_setting('addons.price_per_gb','0');day=db.get_setting('addons.price_per_day','0')
    enter_reply_mode(c,f"<b>⚖️ قیمت‌گذاری تمدید و حجم</b>\nقیمت فعلی هر GB: {fmt(gb)}\nقیمت فعلی هر روز: {fmt(day)}\n\nقیمت جدید را به شکل GB,روز ارسال کنید؛ مثال: 10000,5000",{'step':'addon_prices'},return_to='admin_section:finance')

@bot.callback_query_handler(func=lambda c:c.data=='finance')
def finance(c):
    if not admin(c.from_user.id):return
    st=db.stats();tops=db.top_users(5);buyers=db.top_buyers(5);depositors=db.top_depositors(5)
    txt=f"فروش موفق: {fmt(st['revenue'])}\nسفارش‌ها: {st['orders']}\nرسیدهای معلق: {st['pending_deposits']}\n\n<b>بیشترین خرید</b>\n"
    txt+='\n'.join(f"{u['id']} — {fmt(u['purchased'])}" for u in buyers)
    txt+='\n\n<b>بیشترین شارژ</b>\n'+'\n'.join(f"{u['id']} — {fmt(u['deposited'])}" for u in depositors)
    txt+='\n\n<b>بیشترین موجودی کیف پول</b>\n'+'\n'.join(f"{u['id']} — {fmt(u['balance'])}" for u in tops)
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('🧾 رسیدهای معلق',callback_data='admin_deposits'))
    kb.add(types.InlineKeyboardButton('⚖️ قیمت تمدید/حجم',callback_data='addon_prices'))
    kb.add(types.InlineKeyboardButton('✏️ راهنمای پرداخت',callback_data='edittext:payment'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به مالی',callback_data='admin_section:finance'))
    edit_or_send(c.message.chat.id,'<b>💳 گزارش مالی</b>\n'+safe(txt),kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='lottery_draw')
def lottery_draw(c):
    if not admin(c.from_user.id):return
    uid=db.lottery_winner()
    if not uid:return bot.answer_callback_query(c.id,'شرکت‌کننده‌ای ثبت نشده است.',show_alert=True)
    db.audit(c.from_user.id,'lottery.draw',str(uid))
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به رشد و بازاریابی',callback_data='admin_section:growth'))
    edit_or_send(c.message.chat.id,f'🎉 برنده قرعه‌کشی: <code>{uid}</code>',kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='channel_post')
def channel_post_start(c):
    if not admin(c.from_user.id):return
    enter_reply_mode(c,'<b>📣 ارسال پست کانال</b>\nمتن پست را ارسال کنید. مقصد باید با kind=announcement ثبت شده باشد.',{'step':'channel_post'},return_to='admin_section:support')

@bot.callback_query_handler(func=lambda c:c.data=='categories')
def categories_admin(c):
    if not admin(c.from_user.id): return
    rows=db.categories();kb=types.InlineKeyboardMarkup(row_width=2)
    for x in rows: kb.add(types.InlineKeyboardButton(f'🗂 #{x["id"]} · {x["name"][:28]}',callback_data=f'catdel:{x["id"]}'))
    kb.add(types.InlineKeyboardButton('➕ افزودن دسته',callback_data='catadd'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به فروشگاه و سرویس‌ها',callback_data='admin_section:store'))
    text='<b>🗂 دسته‌بندی‌ها</b>\nبرای حذف، دسته موردنظر را انتخاب کنید.'
    edit_or_send(c.message.chat.id,text,kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='catadd')
def category_add(c):
    if not admin(c.from_user.id): return
    enter_reply_mode(c,'<b>🗂 دسته‌بندی جدید</b>\nنام دسته را ارسال کنید.',{'step':'category_add'},return_to='admin_section:store')

@bot.callback_query_handler(func=lambda c:c.data.startswith('catdel:'))
def category_del(c):
    if not admin(c.from_user.id): return
    db.delete_category(int(c.data.split(':')[1]));db.audit(c.from_user.id,'category.delete',c.data);bot.answer_callback_query(c.id,'دسته حذف شد.')
    categories_admin(c)

@bot.callback_query_handler(func=lambda c:c.data=='discounts')
def discounts_admin(c):
    if not admin(c.from_user.id): return
    rows=db.discounts();kb=types.InlineKeyboardMarkup(row_width=1)
    for x in rows: kb.add(types.InlineKeyboardButton(f"{x['code']} — {x['percent']}% — {'فعال' if x['active'] else 'خاموش'}",callback_data=f'discount_toggle:{x["code"]}'))
    kb.add(types.InlineKeyboardButton('➕ ساخت کد تخفیف',callback_data='discount_add'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به مالی و پرداخت',callback_data='admin_section:finance'))
    edit_or_send(c.message.chat.id,'<b>🎟 مدیریت کدهای تخفیف</b>\nبرای روشن/خاموش کردن هر کد، آن را انتخاب کنید.',kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='discount_add')
def discount_add(c):
    if not admin(c.from_user.id): return
    enter_reply_mode(c,'<b>🎟 ساخت کد تخفیف</b>\nکد، درصد، سقف استفاده را با کاما بفرستید.\nنمونه: SONAR20,20,100',{'step':'discount_add'},return_to='admin_section:finance')

@bot.callback_query_handler(func=lambda c:c.data.startswith('discount_toggle:'))
def discount_toggle(c):
    if not admin(c.from_user.id): return
    code=c.data.split(':',1)[1];x=next((x for x in db.discounts() if x['code']==code),None)
    if x: db.toggle_discount(code,not bool(x['active']))
    db.audit(c.from_user.id,'discount.toggle',code);bot.answer_callback_query(c.id,'به‌روزرسانی شد.');discounts_admin(c)

@bot.callback_query_handler(func=lambda c:c.data=='gift_codes')
def gifts_admin(c):
    if not admin(c.from_user.id): return
    rows=db.gift_codes();kb=types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton('➕ ساخت کد هدیه',callback_data='gift_add'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به مالی و پرداخت',callback_data='admin_section:finance'))
    txt='<b>🎁 کدهای هدیه</b>\n'+'\n'.join(f"<code>{safe(x['code'])}</code> · {fmt(x['amount'])} · مصرف {x['used_count']}/{x['usage_limit']}" for x in rows)
    edit_or_send(c.message.chat.id,txt,kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='gift_add')
def gift_add(c):
    if not admin(c.from_user.id): return
    enter_reply_mode(c,'<b>🎁 ساخت کد هدیه</b>\nکد، مبلغ اعتبار کیف پول و سقف مصرف را با کاما ارسال کنید.\nنمونه: WELCOME50,50000,100',{'step':'gift_add'},return_to='admin_section:finance')

@bot.callback_query_handler(func=lambda c:c.data=='resellers')
def resellers_admin(c):
    if not admin(c.from_user.id): return
    rows=db.resellers();kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('➕ افزودن/ویرایش نماینده',callback_data='reseller_add'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به رشد و بازاریابی',callback_data='admin_section:growth'))
    details='\n'.join(f"<code>{x['user_id']}</code> — {x['commission_percent']}% — {'فعال' if x['active'] else 'غیرفعال'}" for x in rows) or 'هنوز نماینده‌ای ثبت نشده است.'
    edit_or_send(c.message.chat.id,'<b>🤝 نمایندگان</b>\n'+details,kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='reseller_requests')
def reseller_requests(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    rows=db.pending_reseller_requests()
    kb=types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        kb.add(types.InlineKeyboardButton(f"درخواست #{row['id']} · کاربر {row['user_id']}",callback_data=f"reseller_request_review:{row['id']}"))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به رشد و بازاریابی',callback_data='admin_section:growth'))
    edit_or_send(c.message.chat.id,f"📨 <b>درخواست‌های نمایندگی</b>\nدر انتظار بررسی: <b>{len(rows)}</b>",kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('reseller_request_review:'))
def reseller_request_review(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    rid=int(c.data.split(':',1)[1])
    row=next((x for x in db.pending_reseller_requests() if x['id']==rid),None)
    if not row:return bot.answer_callback_query(c.id,'درخواست پیدا نشد یا قبلاً بررسی شده است.',show_alert=True)
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.row(types.InlineKeyboardButton('✅ تأیید نمایندگی',callback_data=f'reseller_request_decide:approve:{rid}'),
           types.InlineKeyboardButton('❌ رد درخواست',callback_data=f'reseller_request_decide:reject:{rid}'))
    kb.add(types.InlineKeyboardButton('⬅️ فهرست درخواست‌ها',callback_data='reseller_requests'))
    edit_or_send(c.message.chat.id,f"📨 <b>درخواست نمایندگی #{rid}</b>\nکاربر: <code>{row['user_id']}</code>\n\n{safe(row['description'])}",kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('reseller_request_decide:'))
def reseller_request_decide(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    _,decision,rid=c.data.split(':')
    approved=decision=='approve'
    commission=int(db.get_setting('reseller.default_commission','10') or 10)
    row=db.decide_reseller_request(int(rid),approved,c.from_user.id,commission)
    if not row:return bot.answer_callback_query(c.id,'این درخواست قبلاً تعیین تکلیف شده است.',show_alert=True)
    db.audit(c.from_user.id,'reseller.request_decide',rid,decision)
    try:
        bot.send_message(row['user_id'],'درخواست نمایندگی شما تأیید شد. پنل نمایندگی از منوی اصلی در دسترس است.' if approved else 'درخواست نمایندگی شما تأیید نشد. برای اطلاعات بیشتر با پشتیبانی تماس بگیرید.')
    except Exception:pass
    bot.answer_callback_query(c.id,'نتیجه ثبت شد.')
    return reseller_requests(c)

@bot.callback_query_handler(func=lambda c:c.data=='reseller_add')
def reseller_add(c):
    if not admin(c.from_user.id): return
    enter_reply_mode(c,'<b>🤝 ثبت نماینده</b>\nشناسه عددی نماینده و درصد کمیسیون را با کاما ارسال کنید.\nنمونه: 123456789,10',{'step':'reseller_add'},return_to='admin_section:growth')

@bot.callback_query_handler(func=lambda c:c.data=='channels')
def channels_admin(c):
    if not admin(c.from_user.id): return
    rows=db.channels();kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('➕ تنظیم کانال/گروه',callback_data='channel_add'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به پشتیبانی',callback_data='admin_section:support'))
    details='\n'.join(f"{safe(x['kind'])}: <code>{safe(x['chat_id'])}</code>" for x in rows) or 'تنظیمی ثبت نشده است.'
    edit_or_send(c.message.chat.id,'<b>📣 کانال‌ها و گروه‌ها</b>\n'+details,kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='channel_add')
def channel_add(c):
    if not admin(c.from_user.id): return
    enter_reply_mode(c,'<b>📣 ثبت کانال یا گروه</b>\nنوع و شناسه کانال را با کاما ارسال کنید.\nنمونه: required,@channel',{'step':'channel_add'},return_to='admin_section:support')

@bot.callback_query_handler(func=lambda c: c.data == 'button_manager')
def button_manager(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    kb = types.InlineKeyboardMarkup(row_width=2)
    keys = [('shop','خرید سرویس'),('profile','مشخصات کاربری'),('orders','سرویس‌های من'),('deposit','شارژ کیف پول'),('support','تیکت پشتیبانی'),('gift','کد هدیه'),('tutorials','آموزش'),('faq','سوالات متداول')]
    lines = []
    buttons = []
    for key, label in keys:
        state = db.get_setting('button.' + key + '.enabled', '1')
        title = db.get_setting('button.' + key + '.label', label) or label
        icon = '🟢' if state == '1' else '⚪'
        lines.append(f'{icon} {safe(title)}')
        buttons.append(types.InlineKeyboardButton(f'{icon} {title[:35]}', callback_data=f'button_manage:{key}'))
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i+2])
    try:
        custom = json.loads(db.get_setting('custom_buttons', '[]'))
        for i, item in enumerate(custom[:8]):
            kb.add(types.InlineKeyboardButton('🗑 حذف لینک: ' + item.get('label', 'دکمه')[:28], callback_data=f'button_delete:{i}'))
    except Exception:
        pass
    kb.add(types.InlineKeyboardButton('➕ افزودن لینک سفارشی', callback_data='button_add'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به ظاهر و تجربه کاربری', callback_data='admin_section:interface'))
    text = '<b>🔘 مدیریت دکمه‌ها</b>\nهر دکمه را باز کنید تا عنوان و وضعیت آن را جداگانه ویرایش کنید.\n\n' + '\n'.join(lines)
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith('button_manage:'))
def button_manage(c):
    if not admin(c.from_user.id):
        return bot.answer_callback_query(c.id, 'دسترسی ندارید.', show_alert=True)
    key = c.data.split(':', 1)[1]
    defaults = {'shop':'خرید سرویس','profile':'مشخصات کاربری','orders':'سرویس‌های من','deposit':'شارژ کیف پول','support':'تیکت پشتیبانی','gift':'کد هدیه','tutorials':'آموزش','faq':'سوالات متداول'}
    if key not in defaults:
        return bot.answer_callback_query(c.id, 'دکمه پیدا نشد.')
    title = db.get_setting('button.' + key + '.label', defaults[key]) or defaults[key]
    enabled = db.get_setting('button.' + key + '.enabled', '1') == '1'
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('🔄 فعال/غیرفعال', callback_data=f'button_toggle:{key}'), types.InlineKeyboardButton('✏️ تغییر عنوان', callback_data=f'button_edit:{key}'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به فهرست دکمه‌ها', callback_data='button_manager'))
    text = f'<b>🔘 تنظیم دکمه</b>\nعنوان فعلی: <b>{safe(title)}</b>\nوضعیت: {"🟢 فعال" if enabled else "⚪ غیرفعال"}'
    edit_or_send(c.message.chat.id, text, kb, c.message.message_id)


@bot.callback_query_handler(func=lambda c:c.data.startswith('button_toggle:'))
def button_toggle(c):
    if not admin(c.from_user.id): return
    key=c.data.split(':',1)[1];old=db.get_setting('button.'+key+'.enabled','1');db.set_setting('button.'+key+'.enabled','0' if old=='1' else '1')
    db.audit(c.from_user.id,'button.toggle',key,'1' if old=='0' else '0');bot.answer_callback_query(c.id,'ذخیره شد.');button_manager(c)

@bot.callback_query_handler(func=lambda c:c.data=='bulk_accounts')
def bulk_accounts(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    return bot.answer_callback_query(c.id,'ساخت گروهی واقعی روی پنل هنوز منتقل نشده است؛ این گزینه در نسخه فعلی غیرفعال است.',show_alert=True)

@bot.callback_query_handler(func=lambda c:c.data.startswith('button_edit:'))
def button_edit(c):
    if not admin(c.from_user.id): return
    key=c.data.split(':',1)[1]
    enter_reply_mode(c,'<b>✏️ ویرایش عنوان دکمه</b>\nعنوان جدید را ارسال کنید.',{'step':'button_edit','key':key},return_to='admin_section:interface')

@bot.callback_query_handler(func=lambda c:c.data.startswith('button_delete:'))
def button_delete(c):
    if not admin(c.from_user.id): return
    i=int(c.data.split(':')[1]);custom=json.loads(db.get_setting('custom_buttons','[]'))
    if i<0 or i>=len(custom):return bot.answer_callback_query(c.id,'دکمه پیدا نشد.')
    label=custom[i].get('label','لینک سفارشی')
    enter_reply_mode(c,f'<b>🗑 تأیید حذف لینک</b>\nعنوان لینک: <b>{safe(label)}</b>\n\nاین لینک از منوی کاربران حذف خواهد شد.',{'step':'confirm_button_delete','index':i,'label':label,'url':custom[i].get('url')},rows=[(REPLY_CONFIRM_DELETE_LINK,),(REPLY_BACK,REPLY_CANCEL)],return_to='admin_section:interface')


@bot.callback_query_handler(func=lambda c:c.data=='button_add')
def button_add(c):
    if not admin(c.from_user.id): return
    enter_reply_mode(c,'<b>➕ افزودن لینک سفارشی</b>\nعنوان و URL را با کاما ارسال کنید. فقط HTTPS.\nمثال: پنل وب,https://example.com',{'step':'button_add'},return_to='admin_section:interface')

@bot.callback_query_handler(func=lambda c:c.data=='reports')
def reports(c):
    if not admin(c.from_user.id): return
    st=db.stats();top=db.top_users(5);buyers=db.top_buyers(5);depositors=db.top_depositors(5);active=db.top_active_services(5);aud=db.recent_audit(15)
    txt=f"کاربران: {st['users']} | سفارش موفق: {st['orders']}\nفروش: {fmt(st['revenue'])}\nرسید معلق: {st['pending_deposits']} | تیکت باز: {st['open_tickets']}\n\n<b>بیشترین خرید</b>\n"
    txt+='\n'.join(f"{x['id']} @{x['username'] or '-'} — {fmt(x['purchased'])} ({x['orders']} سفارش)" for x in buyers)
    txt+='\n\n<b>بیشترین شارژ</b>\n'+'\n'.join(f"{x['id']} — {fmt(x['deposited'])}" for x in depositors)
    txt+='\n\n<b>بیشترین سرویس فعال</b>\n'+'\n'.join(f"{x['id']} — {x['active_services']}" for x in active)
    txt+='\n\n<b>بیشترین موجودی کیف پول</b>\n'+'\n'.join(f"{x['id']} — {fmt(x['balance'])}" for x in top)
    txt+='\n\n<b>آخرین رویدادها</b>\n'+'\n'.join(f"{x['created_at']} | {x['actor_id']} | {x['action']}" for x in aud)
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به نمای کلی',callback_data='admin_section:overview'))
    edit_or_send(c.message.chat.id,'<b>📈 گزارش عملکرد</b>\n'+safe(txt),kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='audit')
def audit(c):
    if not admin(c.from_user.id): return
    rows=db.recent_audit(20)
    txt='\n'.join(f"{x['created_at']} · {x['actor_id']} · {x['action']} · {x['target'] or '-'}" for x in rows)
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('⬅️ بازگشت به نمای کلی',callback_data='admin_section:overview'))
    edit_or_send(c.message.chat.id,'<b>🧾 رویدادهای اخیر</b>\n'+safe(txt or 'لاگی وجود ندارد.'),kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data=='user_search')
def user_search_start(c):
    if not admin(c.from_user.id): return
    if not feature('user_search'):return bot.answer_callback_query(c.id,'جستجوی کاربران غیرفعال است.',show_alert=True)
    enter_reply_mode(c, '<b>🔎 جستجوی کاربر</b>\nشناسه عددی، نام یا username کاربر را وارد کنید.', {'step':'user_search'}, return_to='admin_section:users')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='user_search')
def user_search_result(m):
    if not admin(m.from_user.id): return
    q=(m.text or '').strip();rows=db.search_users(q,25);states.pop(m.from_user.id,None)
    if not rows:
        return finish_reply_mode(m.chat.id,m.from_user.id,'کاربری با این مشخصات یافت نشد.','admin_section:users')
    remove_reply_keyboard(m.chat.id,'نتایج جستجو آماده شد.')
    kb=types.InlineKeyboardMarkup(row_width=1)
    for u in rows:
        kb.add(types.InlineKeyboardButton(f"{u['id']} | @{u['username'] or '-'} | {fmt(u['balance'])}",callback_data=f"user_detail:{u['id']}"))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به کاربران',callback_data='admin_section:users'))
    edit_or_send(m.chat.id,'<b>🔎 نتایج جستجو</b>\nبرای باز کردن پرونده، کاربر را انتخاب کنید.',kb)

@bot.callback_query_handler(func=lambda c:c.data.startswith('user_detail:'))
def user_detail(c):
    if not admin(c.from_user.id): return
    uid=int(c.data.split(':')[1]);u=db.get_user(uid)
    if not u:return bot.answer_callback_query(c.id,'کاربر پیدا نشد.')
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.row(types.InlineKeyboardButton('⛔ مسدود/آزاد',callback_data=f'user_block_toggle:{uid}'),types.InlineKeyboardButton('💳 شارژ کیف پول',callback_data=f'user_credit_start:{uid}'))
    kb.add(types.InlineKeyboardButton('✉️ پیام خصوصی',callback_data=f'user_message_start:{uid}'))
    kb.add(types.InlineKeyboardButton('⬅️ بازگشت به کاربران',callback_data='admin_section:users'))
    edit_or_send(c.message.chat.id,f"<b>👤 کاربر {uid}</b>\nنام: {safe(u['first_name'] or '-')}\nنام کاربری: @{safe(u['username'] or '-')}\nموجودی: {fmt(u['balance'])}\nوضعیت: {'🔴 مسدود' if u['is_blocked'] else '🟢 فعال'}",kb,c.message.message_id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('user_message_start:'))
def user_message_start(c):
    if not admin(c.from_user.id): return
    uid=int(c.data.split(':')[1]);enter_reply_mode(c,'<b>✉️ پیام خصوصی</b>\nمتن پیام برای کاربر '+str(uid)+' را ارسال کنید.',{'step':'user_message','target':uid},return_to='admin_section:users')

@bot.callback_query_handler(func=lambda c:c.data.startswith('user_block_toggle:'))
def user_block_toggle(c):
    if not admin(c.from_user.id): return
    uid=int(c.data.split(':')[1]);u=db.get_user(uid)
    if u:
        db.set_user_blocked(uid,not bool(u['is_blocked']))
        db.audit(c.from_user.id,'user.block',str(uid),str(not bool(u['is_blocked'])))
        bot.answer_callback_query(c.id,'وضعیت تغییر کرد.')
        user_detail(c)
    else:
        bot.answer_callback_query(c.id,'کاربر پیدا نشد.',show_alert=True)

@bot.callback_query_handler(func=lambda c:c.data.startswith('user_credit_start:'))
def user_credit_start(c):
    if not admin(c.from_user.id): return
    uid=int(c.data.split(':')[1]);enter_reply_mode(c,'<b>💸 شارژ کیف پول</b>\nمبلغ افزایش موجودی کاربر '+str(uid)+' را به '+settings.currency_label+' وارد کنید.',{'step':'credit','values':[str(uid)]},return_to='admin_section:users')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step') in {'category_add','discount_add','gift_add','reseller_add','channel_add','button_edit','button_add','channel_post','user_message','addon_prices','trial_settings','custom_plan_settings','cashback_settings','wheel_settings'})
def advanced_admin_input(m):
    if not admin(m.from_user.id): return
    st=states.get(m.from_user.id,{});step=st.get('step');value=(m.text or '').strip()
    try:
        if step=='trial_settings':
            gb,days,max_uses,groups=value.split(',',3)
            gb=int(gb);days=int(days);max_uses=int(max_uses);group_ids=json.loads(groups)
            if gb<0 or gb>10000 or days<0 or days>3650 or (gb==0 and days==0) or not 1<=max_uses<=1000:raise ValueError('حجم باید ۰ تا ۱۰۰۰۰، روز ۰ تا ۳۶۵۰ و سهمیه ۱ تا ۱۰۰۰ باشد؛ حجم و روز هم‌زمان صفر نباشند.')
            if (gb==0 or days==0) and not feature('unlimited_plan','0'):raise ValueError('برای اکانت تست نامحدود، ابتدا قابلیت پلن نامحدود را فعال کنید.')
            if not isinstance(group_ids,list) or any(type(x) is not int or x<0 for x in group_ids):raise ValueError('GroupIDs باید آرایه‌ای از اعداد صحیح نامنفی باشد.')
            db.set_setting('trial.gb',gb);db.set_setting('trial.days',days);db.set_setting('trial.max_per_user',max_uses);db.set_setting('trial.group_ids',json.dumps(group_ids))
            db.trial_product(gb,days,json.dumps(group_ids));db.audit(m.from_user.id,'trial.settings',None,value)
            return finish_reply_mode(m.chat.id,m.from_user.id,'تنظیمات اکانت تست ذخیره شد.','admin_section:store')
        if step=='custom_plan_settings':
            per_gb,per_day,groups=value.split(',',2);per_gb=int(per_gb);per_day=int(per_day);group_ids=json.loads(groups)
            if not 1<=per_gb<=10**10 or not 0<=per_day<=10**10:raise ValueError('قیمت هر GB باید مثبت و قیمت هر روز نامنفی و در محدوده مجاز باشد.')
            if not isinstance(group_ids,list) or any(type(x) is not int or x<0 for x in group_ids):raise ValueError('GroupIDs باید آرایه‌ای از اعداد صحیح نامنفی باشد.')
            db.set_setting('custom.price_per_gb',per_gb);db.set_setting('custom.price_per_day',per_day);db.set_setting('custom.group_ids',json.dumps(group_ids))
            db.audit(m.from_user.id,'custom_plan.settings',None,value)
            return finish_reply_mode(m.chat.id,m.from_user.id,'قیمت‌گذاری پلن دلخواه ذخیره شد.','admin_section:store')
        if step=='cashback_settings':
            purchase,deposit,referral=[int(x.strip()) for x in value.split(',')]
            if any(x<0 or x>100 for x in [purchase,deposit,referral]):raise ValueError('درصدها باید بین ۰ تا ۱۰۰ باشند.')
            db.set_setting('cashback.purchase_percent',purchase);db.set_setting('cashback.deposit_percent',deposit);db.set_setting('referral.purchase_percent',referral)
            db.audit(m.from_user.id,'cashback.settings',None,value)
            return finish_reply_mode(m.chat.id,m.from_user.id,'درصدهای کش‌بک و کمیسیون ذخیره شد.','admin_section:finance')
        if step=='wheel_settings':
            reward,hours,mode,first_only=[x.strip() for x in value.split(',')]
            reward=int(reward);hours=int(hours)
            if reward<0 or not 1<=hours<=720 or mode not in {'dice','slot'} or first_only not in {'0','1'}:raise ValueError('مقادیر نامعتبر است؛ حالت dice یا slot و بازه انتظار ۱ تا ۷۲۰ ساعت است.')
            db.set_setting('wheel.reward',reward);db.set_setting('wheel.cooldown_hours',hours);db.set_setting('wheel.mode',mode);db.set_setting('wheel.first_purchase_only',first_only)
            db.audit(m.from_user.id,'wheel.settings',None,value)
            return finish_reply_mode(m.chat.id,m.from_user.id,'تنظیمات چرخ شانس ذخیره شد.','admin_section:growth')
        if step=='addon_prices':
            gb,day=[int(x.strip()) for x in value.split(',')]
            if gb<0 or day<0: raise ValueError('قیمت‌ها نمی‌توانند منفی باشند.')
            db.set_setting('addons.price_per_gb',str(gb));db.set_setting('addons.price_per_day',str(day))
            db.audit(m.from_user.id,'addons.prices',None,f'gb={gb};day={day}')
            return finish_reply_mode(m.chat.id,m.from_user.id,'قیمت‌های افزونه ذخیره شد.','admin_section:finance')
        if step=='user_message':
            target=st['target'];bot.send_message(target,safe(value))
            db.audit(m.from_user.id,'user.private_message',str(target),'sent')
            return finish_reply_mode(m.chat.id,m.from_user.id,'پیام خصوصی ارسال شد.','admin_section:users')
        if step=='channel_post':
            dest=next((x['chat_id'] for x in db.channels() if x['kind']=='announcement'),None)
            if not dest: raise ValueError('ابتدا مقصد با kind=announcement در مدیریت کانال‌ها تنظیم شود.')
            bot.send_message(dest,value)
            db.audit(m.from_user.id,'channel.post',dest,'sent')
            return finish_reply_mode(m.chat.id,m.from_user.id,'پست کانال ارسال شد.','admin_section:support')
        if step=='button_edit':
            if not value or len(value)>42: raise ValueError('عنوان دکمه باید بین ۱ تا ۴۲ نویسه باشد.')
            db.set_setting('button.'+st['key']+'.label',value);db.audit(m.from_user.id,'button.label',st['key']);return finish_reply_mode(m.chat.id,m.from_user.id,'عنوان دکمه ذخیره شد.','admin_section:interface')
        if step=='button_add':
            label,url=[x.strip() for x in value.split(',',1)]
            if not label or len(label)>42: raise ValueError('عنوان لینک باید بین ۱ تا ۴۲ نویسه باشد.')
            if not url.startswith('https://'): raise ValueError('URL باید با https:// شروع شود.')
            custom=json.loads(db.get_setting('custom_buttons','[]'));custom.append({'label':label[:50],'url':url})
            db.set_setting('custom_buttons',json.dumps(custom,ensure_ascii=False));db.audit(m.from_user.id,'button.custom_add',label,url);return finish_reply_mode(m.chat.id,m.from_user.id,'دکمه لینک‌دار افزوده شد.','admin_section:interface')
        if step=='category_add':
            if not value or len(value)>80: raise ValueError('نام دسته باید بین ۱ تا ۸۰ نویسه باشد.')
            db.add_category(value);db.audit(m.from_user.id,'category.add',value);return finish_reply_mode(m.chat.id,m.from_user.id,'دسته‌بندی ایجاد شد.','admin_section:store')
        if step=='discount_add':
            code,percent,limit=[x.strip() for x in value.split(',')]
            percent=int(percent);limit=int(limit)
            if not 1<=percent<=100 or limit<0: raise ValueError('درصد باید ۱ تا ۱۰۰ و سقف استفاده نامنفی باشد.')
            db.add_discount(code,percent,limit);db.audit(m.from_user.id,'discount.add',code);return finish_reply_mode(m.chat.id,m.from_user.id,'کد تخفیف ایجاد شد.','admin_section:finance')
        if step=='gift_add':
            code,amount,limit=[x.strip() for x in value.split(',')]
            amount=int(amount);limit=int(limit)
            if not code or amount<=0 or limit<1: raise ValueError('کد غیرخالی، مبلغ مثبت و سقف مصرف حداقل ۱ لازم است.')
            db.add_gift_code(code,amount,0,0,limit);db.audit(m.from_user.id,'gift.add',code);return finish_reply_mode(m.chat.id,m.from_user.id,'کد هدیه اعتباری ایجاد شد.','admin_section:finance')
        if step=='reseller_add':
            uid,commission=[int(x.strip()) for x in value.split(',')]
            if not 0<=commission<=100: raise ValueError('کمیسیون باید بین ۰ تا ۱۰۰ باشد.')
            db.set_reseller(uid,commission,True);db.audit(m.from_user.id,'reseller.set',str(uid),commission);return finish_reply_mode(m.chat.id,m.from_user.id,'نماینده ذخیره شد.','admin_section:growth')
        if step=='channel_add':
            kind,chat=[x.strip() for x in value.split(',',1)]
            db.save_channel(kind,chat);db.audit(m.from_user.id,'channel.set',kind,chat);return finish_reply_mode(m.chat.id,m.from_user.id,'کانال یا گروه ذخیره شد.','admin_section:support')
        if step=='bulk_accounts':
            n,gb,days,groups=value.split(',',3);n=int(n);gb=int(gb);days=int(days);json.loads(groups)
            if not 1<=n<=50 or gb<0 or days<0: raise ValueError('تعداد ۱ تا ۵۰ و حجم/روز نامنفی باشد.')
            return finish_reply_mode(m.chat.id,m.from_user.id,'درخواست گروهی دریافت شد؛ اجرای واقعی هنوز به Job اجرایی متصل نیست.','admin_section:store')
    except Exception as e:
        return bot.send_message(m.chat.id,'ورودی نامعتبر: '+safe(e))

@bot.message_handler(commands=['help'])
def help_command(m):
    remove_reply_keyboard(m.chat.id, '<b>راهنمای SonarX</b>\n/start نمایش پنل کاربری\n/panel ورود به مدیریت (ویژه مدیران)\n/cancel لغو مرحله جاری')
    edit_or_send(m.chat.id, home_text(m.from_user.id), home(m.from_user.id))

def main():
    log.info('SonarX starting; backend=%s',db.backend)
    bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
if __name__=='__main__':main()
