import json, logging, secrets, re, time, random
from telebot import TeleBot, types
from .config import settings
from .db import Database
from .security import SecretBox
from .pasarguard import PasarGuardClient, MarzbanClient, MarzneshinClient, PasarGuardError

settings.validate()
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
log=logging.getLogger('sonarx')
bot=TeleBot(settings.bot_token,parse_mode='HTML',threaded=True,num_threads=8)
db=Database(settings.database_url or settings.database_path)
box=SecretBox(settings.fernet_key) if settings.fernet_key else None
states={}

def admin(uid):
    if uid in settings.admin_ids:return True
    try:return db.is_admin(uid)
    except Exception:return False

def safe(v):return str(v).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
def fmt(v):return f'{int(v):,} {settings.currency_label}'
def feature(key,default='1'):return db.get_setting('feature.'+key,default)=='1'
def text_setting(key,default):return db.get_setting('text.'+key,default)
def server_client(row):
    if not box:raise RuntimeError('FERNET_KEY تنظیم نشده است.')
    cls={'pasarguard':PasarGuardClient,'marzban':MarzbanClient,'marzneshin':MarzneshinClient}.get(row.get('panel_type','pasarguard'))
    if not cls:raise RuntimeError('نوع پنل پشتیبانی نمی‌شود: '+str(row.get('panel_type')))
    return cls(row['url'],row['username'],box.decrypt(row['password_enc']),settings.verify_tls)
def user_reply_keyboard(uid):
    # Telegram supports one reply_markup per message. We keep this persistent
    # reply keyboard for navigation and use inline keyboards in each feature screen.
    k=types.ReplyKeyboardMarkup(resize_keyboard=True,row_width=2)
    def enabled(key): return db.get_setting('button.'+key+'.enabled','1')=='1'
    row=[]
    for key,label in [('shop','🛒 خرید سرویس'),('profile','👤 حساب من'),('orders','📦 سرویس‌های من'),('deposit','💳 افزایش موجودی'),('support','🎫 پشتیبانی'),('gift','🎁 کد هدیه'),('tutorials','📚 آموزش اتصال'),('faq','❓ سوالات متداول')]:
        if enabled(key):
            row.append(types.KeyboardButton(label))
            if len(row)==2: k.add(*row);row=[]
    if row:k.add(*row)
    k.add(types.KeyboardButton('🎟 تیکت‌های من'),types.KeyboardButton('🎲 شانس و جایزه'))
    if admin(uid): k.add(types.KeyboardButton('⚙️ پنل مدیریت'))
    return k

def home(uid):
    k=types.InlineKeyboardMarkup(row_width=2)
    def label(key,default):
        return db.get_setting('button.'+key+'.label',default)
    def enabled(key):
        return db.get_setting('button.'+key+'.enabled','1')=='1'
    if enabled('shop'): k.add(types.InlineKeyboardButton(label('shop','🛒 خرید سرویس'),callback_data='shop'))
    if enabled('profile'): k.add(types.InlineKeyboardButton(label('profile','👤 حساب من'),callback_data='profile'))
    if enabled('orders'): k.add(types.InlineKeyboardButton(label('orders','📦 سرویس‌های من'),callback_data='orders'))
    if enabled('deposit'): k.add(types.InlineKeyboardButton(label('deposit','💳 افزایش موجودی'),callback_data='deposit'))
    if enabled('support'): k.add(types.InlineKeyboardButton(label('support','🎫 پشتیبانی'),callback_data='support'))
    if enabled('gift'): k.add(types.InlineKeyboardButton(label('gift','🎁 کد هدیه'),callback_data='redeem_gift'))
    if enabled('tutorials'): k.add(types.InlineKeyboardButton(label('tutorials','📚 آموزش اتصال'),callback_data='tutorials'))
    if enabled('faq'): k.add(types.InlineKeyboardButton(label('faq','❓ سوالات متداول'),callback_data='faq'))
    if feature('referral','0'): k.add(types.InlineKeyboardButton('💸 کسب درآمد / دعوت دوستان',callback_data='referral'))
    if feature('dice_game','0'): k.add(types.InlineKeyboardButton('🎲 شانس و جایزه',callback_data='dice_game'))
    try:
        for item in json.loads(db.get_setting('custom_buttons','[]'))[:8]:
            if item.get('label') and item.get('url','').startswith('https://'):
                k.add(types.InlineKeyboardButton(item['label'][:50],url=item['url']))
    except Exception: pass
    if admin(uid):k.add(types.InlineKeyboardButton('⚙️ مدیریت SonarX',callback_data='admin'))
    return k

def blocked(uid):
    u=db.get_user(uid); return bool(u and u.get('is_blocked'))
def maintenance(uid):
    if db.get_setting('feature.maintenance','0')=='1' and not admin(uid):
        return True
    return False

def admin_panel(chat_id):
    k=types.InlineKeyboardMarkup(row_width=2)
    buttons=[('📊 داشبورد','stats'),('📈 گزارش‌ها و برترین‌ها','reports'),('🧩 تنظیمات جامع','admin_config'),('🎚 قابلیت‌های ربات','feature_matrix:0'),
      ('📦 پلن‌ها','admin_products'),('🗂 دسته‌بندی‌ها','categories'),('🖥 سرورها و پنل‌ها','admin_servers'),('🧾 رسیدهای معلق','admin_deposits'),('💰 بخش مالی','finance'),
      ('👥 کاربران و جستجو','admin_users'),('➕ پلن جدید','add_product'),('➕ سرور جدید','add_server'),('💰 شارژ کاربر','credit'),
      ('⛔ مسدودسازی','user_block'),('📢 پیام همگانی','broadcast'),('🎫 تیکت‌ها','admin_tickets'),('🛡 مدیران','admin_admins'),
      ('🎟 کد تخفیف','discounts'),('🎁 کد هدیه اعتباری','gift_codes'),('🎲 قرعه‌کشی','lottery_admin'),('🤝 نمایندگان','resellers'),('📣 کانال‌ها','channels'),
      ('🔘 مدیریت دکمه‌ها','button_manager'),('👥 ساخت گروهی اکانت','bulk_accounts'),('⚠️ سفارش‌های بررسی','admin_reviews'),
      ('🧾 لاگ مدیریتی','audit'),('🔙 منوی کاربر','user_home')]
    for label,cb in buttons:k.add(types.InlineKeyboardButton(label,callback_data=cb))
    bot.send_message(chat_id,'<b>مرکز مدیریت SonarX</b>\nمدیریت فروش، مالی، کاربران، پنل‌ها و پشتیبانی.',reply_markup=k)

@bot.message_handler(commands=['start'])
def start(m):
    existing=db.get_user(m.from_user.id)
    u=db.user(m.from_user.id,m.from_user.username,m.from_user.first_name)
    if not existing and len(m.text.split())>1 and feature('referral','0'):
        try:
            inviter=int(m.text.split()[1])
            if db.get_user(inviter):
                bonus=int(db.get_setting('referral_bonus','0') or 0)
                if db.record_referral(inviter,m.from_user.id,max(0,bonus)):
                    try:bot.send_message(inviter,f'یک کاربر با لینک دعوت شما عضو شد. پاداش: {fmt(max(0,bonus))}')
                    except Exception:pass
        except Exception:log.exception('Referral attribution failed')
    if u.get('is_blocked') and not admin(m.from_user.id):return bot.send_message(m.chat.id,'حساب کاربری شما مسدود است.')
    welcome=text_setting('welcome','<b>به SonarX خوش آمدید</b>\nفروشگاه هوشمند سرویس‌های VPN؛ انتخاب پلن، پرداخت و تحویل خودکار.')
    bot.send_message(m.chat.id,welcome,reply_markup=user_reply_keyboard(m.from_user.id))
    bot.send_message(m.chat.id,'از منوی زیر استفاده کنید:',reply_markup=home(m.from_user.id))

@bot.message_handler(commands=['cancel'])
def cancel(m):
    states.pop(m.from_user.id,None);bot.send_message(m.chat.id,'عملیات لغو شد.',reply_markup=home(m.from_user.id))

@bot.callback_query_handler(func=lambda c:c.data=='shop')
def shop(c):
    if maintenance(c.from_user.id):return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    if not feature('shop_enabled'):return bot.answer_callback_query(c.id,'فروشگاه موقتاً غیرفعال است.',show_alert=True)
    items=db.products();k=types.InlineKeyboardMarkup()
    for p in items:k.add(types.InlineKeyboardButton(f"{p['name']} — {fmt(p['price'])}",callback_data=f"buy:{p['id']}"))
    if not items:return bot.answer_callback_query(c.id,'فعلاً پلنی موجود نیست.',show_alert=True)
    try:bot.edit_message_text('<b>فروشگاه SonarX</b>\n'+safe(text_setting('shop_notice','پلن موردنظر را انتخاب کنید:')),c.message.chat.id,c.message.message_id,reply_markup=k)
    except Exception:bot.send_message(c.message.chat.id,'<b>فروشگاه SonarX</b>',reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('buy:'))
def buy(c):
    if maintenance(c.from_user.id):return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    p=db.product(int(c.data.split(':')[1]))
    if not p:return bot.answer_callback_query(c.id,'پلن در دسترس نیست.',show_alert=True)
    k=types.InlineKeyboardMarkup(row_width=2)
    k.add(types.InlineKeyboardButton('تأیید خرید',callback_data=f"confirm:{p['id']}"),types.InlineKeyboardButton('🎟 اعمال تخفیف',callback_data=f"discount_apply:{p['id']}"))
    k.add(types.InlineKeyboardButton('بازگشت','shop'))
    bot.edit_message_text(f"<b>{safe(p['name'])}</b>\n{safe(p['description'])}\nحجم: {p['traffic_gb']} GB | اعتبار: {p['days'] or 'نامحدود'} روز\nقیمت: <b>{fmt(p['price'])}</b>",c.message.chat.id,c.message.message_id,reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('discount_apply:'))
def discount_apply_start(c):
    if maintenance(c.from_user.id):return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    if not feature('discounts','1'):return bot.answer_callback_query(c.id,'کد تخفیف غیرفعال است.',show_alert=True)
    pid=int(c.data.split(':')[1]);states[c.from_user.id]={'step':'discount_input','product_id':pid}
    bot.send_message(c.message.chat.id,'کد تخفیف را ارسال کنید. /cancel برای لغو.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='discount_input')
def discount_input(m):
    st=states.get(m.from_user.id,{})
    code=(m.text or '').strip().upper();d=db.get_discount(code)
    if not d:return bot.send_message(m.chat.id,'کد تخفیف معتبر یا فعال نیست. دوباره تلاش کنید.')
    st['discount_code']=code;st['step']=''
    p=db.product(st['product_id'])
    if not p:return bot.send_message(m.chat.id,'پلن پیدا نشد.')
    price=int(p['price']*(100-d['percent'])/100)
    bot.send_message(m.chat.id,f"کد {safe(code)} اعمال شد. تخفیف: {d['percent']}٪\nقیمت نهایی: <b>{fmt(price)}</b>",reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('تأیید خرید',callback_data=f"confirm:{p['id']}"),types.InlineKeyboardButton('بازگشت','shop')))

@bot.callback_query_handler(func=lambda c:c.data.startswith('confirm:'))
def confirm(c):
    if maintenance(c.from_user.id):return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    p=db.product(int(c.data.split(':')[1]))
    if not p:return bot.answer_callback_query(c.id,'پلن پیدا نشد.',show_alert=True)
    discount_code=states.get(c.from_user.id,{}).get('discount_code')
    reserved=False
    if discount_code:
        percent=db.reserve_discount(discount_code)
        if percent is None:
            states.pop(c.from_user.id,None)
            return bot.answer_callback_query(c.id,'کد تخفیف تمام شده یا غیرفعال است.',show_alert=True)
        p=dict(p);p['price']=int(p['price']*(100-percent)/100);reserved=True
    try:oid=db.begin_order(c.from_user.id,p)
    except Exception as exc:
        if reserved:db.release_discount(discount_code)
        return bot.answer_callback_query(c.id,str(exc),show_alert=True)
    states.pop(c.from_user.id,None)
    bot.answer_callback_query(c.id,'سفارش ثبت شد؛ در حال ساخت سرویس…')
    bot.send_message(c.message.chat.id,'⏳ سفارش شما در حال پردازش است.')
    servers=db.servers()
    if not servers:
        db.fail_order_refund(oid,'No active PasarGuard server')
        if reserved: db.release_discount(discount_code)
        return bot.send_message(c.message.chat.id,'سرور فعالی تنظیم نشده است؛ مبلغ به کیف پول برگشت خورد.',reply_markup=home(c.from_user.id))
    # Authenticate candidate servers first. Once a create POST is attempted, do not fail over blindly.
    selected=None;errors=[]
    for server in servers:
        try:
            client=server_client(server);client.login();selected=(server,client);break
        except Exception as exc:errors.append(f"{server['name']}: {exc}");log.warning('Panel login failed: %s',exc)
    if not selected:
        db.fail_order_refund(oid,'Panel login failed: '+'; '.join(errors))
        if reserved: db.release_discount(discount_code)
        return bot.send_message(c.message.chat.id,'اتصال به هیچ سرور فعالی موفق نبود؛ مبلغ برگشت داده شد.',reply_markup=home(c.from_user.id))
    server,client=selected;username=f"sx{c.from_user.id}_{secrets.token_hex(4)}"
    try:
        groups=json.loads(p['group_ids'] or '[]')
        result,sub=client.create_user(username,p['traffic_gb'],p['days'],groups,note=f'SonarX order {oid}')
    except PasarGuardError as exc:
        if exc.uncertain:
            db.mark_order_review(oid,str(exc));
            bot.send_message(c.message.chat.id,f'⚠️ نتیجه ساخت سرویس قطعی نیست؛ مبلغ محفوظ است و مدیر بررسی می‌کند. شناسه: <code>{oid[:8]}</code>')
            for aid in settings.admin_ids:
                try:bot.send_message(aid,f'سفارش نیازمند بررسی: {oid}\nسرور: {safe(server["name"])}\nکاربر پنل: {safe(username)}\n{safe(exc)}')
                except Exception:pass
        else:
            db.fail_order_refund(oid,str(exc))
            if reserved: db.release_discount(discount_code)
            bot.send_message(c.message.chat.id,'ساخت سرویس ناموفق بود؛ مبلغ به کیف پول برگشت خورد.')
        return
    except Exception as exc:
        db.mark_order_review(oid,f'Unexpected panel error; check panel before refund: {exc}')
        log.exception('Uncertain PasarGuard create result')
        bot.send_message(c.message.chat.id,'⚠️ نتیجه ساخت سرویس نامشخص است؛ مبلغ محفوظ است و سفارش برای بررسی مدیر ثبت شد.')
        return
    try:db.finish_order(oid,server['id'],username,sub)
    except Exception as exc:
        db.mark_order_review(oid,f'Panel user created; DB finalization failed: {exc}')
        log.exception('Order finalization failed');return bot.send_message(c.message.chat.id,'اکانت احتمالاً در پنل ساخته شده؛ ثبت دیتابیس نیازمند بررسی مدیر است و مبلغ محفوظ ماند.')
    bot.send_message(c.message.chat.id,f'✅ <b>سرویس شما ساخته شد</b>\nنام کاربری: <code>{safe(username)}</code>\nلینک اشتراک:\n<code>{safe(sub)}</code>\nشناسه سفارش: <code>{oid[:8]}</code>',reply_markup=home(c.from_user.id))

@bot.callback_query_handler(func=lambda c:c.data.startswith('service_addon:'))
def service_addon_start(c):
    _,kind,sid=c.data.split(':')
    if kind not in {'gb','days'}:return bot.answer_callback_query(c.id,'عملیات نامعتبر است.')
    if kind=='gb' and not feature('increase_volume','0'):return bot.answer_callback_query(c.id,'افزایش حجم غیرفعال است.',show_alert=True)
    if kind=='days' and not feature('renew_service','0'):return bot.answer_callback_query(c.id,'تمدید سرویس غیرفعال است.',show_alert=True)
    svc=db.service_for_user(int(sid),c.from_user.id)
    if not svc:return bot.answer_callback_query(c.id,'سرویس پیدا نشد.',show_alert=True)
    if svc['panel_type']=='marzneshin':return bot.answer_callback_query(c.id,'این عملیات برای پنل Marzneshin هنوز فعال نیست.',show_alert=True)
    states[c.from_user.id]={'step':'service_addon_value','kind':kind,'service_id':int(sid)}
    bot.send_message(c.message.chat.id,'مقدار افزایش را به '+('GB' if kind=='gb' else 'روز')+' وارد کنید. /cancel برای لغو.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='service_addon_value')
def service_addon_apply(m):
    st=states.get(m.from_user.id,{})
    try:
        units=int((m.text or '').strip())
        if not 1<=units<=3650:raise ValueError('مقدار باید بین ۱ تا ۳۶۵۰ باشد.')
        kind=st['kind'];sid=st['service_id'];uid=m.from_user.id
        svc=db.service_for_user(sid,uid)
        if not svc:raise ValueError('سرویس پیدا نشد.')
        price_key='addons.price_per_gb' if kind=='gb' else 'addons.price_per_day'
        unit_price=int(db.get_setting(price_key,'0') or 0)
        if unit_price<=0:raise ValueError('مدیر هنوز قیمت این خدمت را تنظیم نکرده است.')
        amount=unit_price*units
        ref=f"addon:{sid}:{uid}:{secrets.token_hex(8)}"
        db.debit_wallet(uid,amount,ref)
        try:
            try: client=server_client(svc)
            except Exception as local_exc:
                db.add_credit(uid,amount,'refund:'+ref,kind='refund')
                states.pop(uid,None)
                return bot.send_message(m.chat.id,'تنظیمات اتصال پنل معتبر نیست؛ مبلغ به کیف پول برگشت داده شد.')
            result,new_limit,new_expire=client.extend_user(svc['remote_username'],add_gb=units if kind=='gb' else 0,add_days=units if kind=='days' else 0)
            db.update_service_entitlements(sid,uid,new_limit,new_expire)
            db.audit(uid,'service.addon',str(sid),f'{kind}={units};amount={amount}')
            states.pop(uid,None)
            return bot.send_message(m.chat.id,f"تغییر سرویس با موفقیت انجام شد.\nمبلغ: {fmt(amount)}\nموجودی باقی‌مانده: {fmt(db.get_user(uid)['balance'])}")
        except PasarGuardError as exc:
            if not exc.uncertain:
                db.add_credit(uid,amount,'refund:'+ref,kind='refund')
                states.pop(uid,None)
                return bot.send_message(m.chat.id,'پنل تغییر را نپذیرفت؛ مبلغ به کیف پول برگشت داده شد.\n'+safe(exc))
            db.audit(uid,'service.addon.review',str(sid),str(exc))
            for aid in settings.admin_ids:
                try:bot.send_message(aid,f"تغییر سرویس نیازمند بررسی است. user={uid} service={sid} ref={ref}\n{safe(exc)}")
                except Exception:pass
            states.pop(uid,None)
            return bot.send_message(m.chat.id,'نتیجه تغییر در پنل نامشخص است؛ مبلغ فعلاً محفوظ مانده و برای بررسی مدیر ارسال شد.')
        except Exception as exc:
            db.audit(uid,'service.addon.error',str(sid),str(exc))
            states.pop(uid,None)
            return bot.send_message(m.chat.id,'خطای غیرمنتظره رخ داد؛ برای جلوگیری از بازپرداخت اشتباه، مبلغ تا بررسی مدیر محفوظ است.')
    except ValueError as exc:
        return bot.send_message(m.chat.id,'خطا: '+safe(exc))
    except Exception as exc:
        return bot.send_message(m.chat.id,'خطا در ثبت عملیات: '+safe(exc))

@bot.callback_query_handler(func=lambda c:c.data=='profile')
def profile(c):
    u=db.get_user(c.from_user.id)
    if not u:return
    bot.send_message(c.message.chat.id,f"<b>حساب کاربری</b>\nشناسه: <code>{u['id']}</code>\nموجودی: <b>{fmt(u['balance'])}</b>\nوضعیت: {'مسدود' if u['is_blocked'] else 'فعال'}",reply_markup=home(c.from_user.id))

@bot.callback_query_handler(func=lambda c:c.data=='orders')
def orders(c):
    rows=db.services_for(c.from_user.id)
    if rows:
        for s in rows:
            msg=f"<b>{safe(s['product_name'])}</b>\nسرور: {safe(s['server_name'])}\nوضعیت: {safe(s['status'])}\nمصرف: {s['used_bytes']/1024**3:.2f} / {s['data_limit_bytes']/1024**3:.2f} GB"
            if s['expires_at']:msg+=f"\nانقضا: {safe(s['expires_at'])}"
            if s['subscription_url']:msg+=f"\nاشتراک: <code>{safe(s['subscription_url'])}</code>"
            kb=types.InlineKeyboardMarkup(row_width=2)
            if feature('increase_volume','0'): kb.add(types.InlineKeyboardButton('➕ افزایش حجم',callback_data=f'service_addon:gb:{s["id"]}'))
            if feature('renew_service','0'): kb.add(types.InlineKeyboardButton('🗓 تمدید زمان',callback_data=f'service_addon:days:{s["id"]}'))
            bot.send_message(c.message.chat.id,msg,reply_markup=kb if kb.keyboard else None)
    else:
        rows=db.orders_for(c.from_user.id)
        if not rows:return bot.send_message(c.message.chat.id,'هنوز سرویسی ندارید.',reply_markup=home(c.from_user.id))
        for o in rows:bot.send_message(c.message.chat.id,f"سفارش <code>{o['id'][:8]}</code> — {safe(o['product_name'])}\nوضعیت: {safe(o['status'])}")

@bot.callback_query_handler(func=lambda c:c.data=='deposit')
def deposit(c):
    if maintenance(c.from_user.id):return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    if not feature('deposits_enabled'):return bot.answer_callback_query(c.id,'شارژ کیف پول غیرفعال است.',show_alert=True)
    states[c.from_user.id]={'step':'deposit_amount'}
    bot.send_message(c.message.chat.id,text_setting('payment','مبلغ شارژ را به تومان وارد کنید؛ سپس تصویر رسید را ارسال کنید.'))

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='deposit_amount')
def deposit_amount(m):
    try:
        amount=int((m.text or '').replace(',','').strip())
        if amount<=0 or amount>10**10:raise ValueError()
        states[m.from_user.id]={'step':'deposit_receipt','amount':amount};bot.send_message(m.chat.id,'تصویر رسید پرداخت را ارسال کنید. /cancel برای لغو.')
    except Exception:bot.send_message(m.chat.id,'مبلغ معتبر نیست؛ عدد مثبت وارد کنید.')

@bot.message_handler(content_types=['photo'],func=lambda m:states.get(m.from_user.id,{}).get('step')=='deposit_receipt')
def deposit_receipt(m):
    st=states.pop(m.from_user.id);did=db.create_deposit(m.from_user.id,st['amount'],m.photo[-1].file_id)
    bot.send_message(m.chat.id,f'رسید ثبت شد. شماره پیگیری: <code>{did}</code>')
    k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('تأیید',callback_data=f'dep:yes:{did}'),types.InlineKeyboardButton('رد',callback_data=f'dep:no:{did}'))
    for aid in settings.admin_ids:
        try:bot.send_photo(aid,m.photo[-1].file_id,caption=f'رسید #{did}\nکاربر: {m.from_user.id}\nمبلغ: {fmt(st["amount"])}',reply_markup=k)
        except Exception:log.exception('Receipt notification failed')

@bot.callback_query_handler(func=lambda c:c.data.startswith('dep:'))
def decide_deposit(c):
    if not admin(c.from_user.id):return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    _,decision,did=c.data.split(':');d=db.decide_deposit(int(did),decision=='yes',c.from_user.id)
    if not d:return bot.answer_callback_query(c.id,'این رسید قبلاً تعیین تکلیف شده است.',show_alert=True)
    db.audit(c.from_user.id,'deposit.decide',did,decision)
    bot.answer_callback_query(c.id,'ثبت شد.')
    try:bot.send_message(d['user_id'],'شارژ کیف پول تأیید شد.' if decision=='yes' else 'رسید پرداخت رد شد.')
    except Exception:pass
    try:bot.edit_message_caption((c.message.caption or '')+'\nتعیین تکلیف شد.',c.message.chat.id,c.message.message_id)
    except Exception:pass

@bot.callback_query_handler(func=lambda c:c.data=='support')
def support(c):
    if not feature('support_enabled'):return bot.answer_callback_query(c.id,'پشتیبانی موقتاً غیرفعال است.',show_alert=True)
    states[c.from_user.id]={'step':'ticket_subject'};bot.send_message(c.message.chat.id,'موضوع تیکت را ارسال کنید. /cancel برای لغو.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='ticket_subject')
def ticket_subject(m):
    states[m.from_user.id]={'step':'ticket_body','subject':(m.text or 'پشتیبانی')[:180]};bot.send_message(m.chat.id,'متن درخواست را ارسال کنید.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='ticket_body')
def ticket_body(m):
    st=states.pop(m.from_user.id);tid=db.add_ticket(m.from_user.id,st['subject'],m.text or '')
    bot.send_message(m.chat.id,f'تیکت شما با شماره #{tid} ثبت شد.')
    for aid in settings.admin_ids:
        try:bot.send_message(aid,f'تیکت جدید #{tid} از کاربر {m.from_user.id}: {safe(st["subject"])}',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('مشاهده',callback_data=f'ticket:{tid}')))
        except Exception:pass

@bot.callback_query_handler(func=lambda c:c.data=='admin')
def admin_entry(c):
    if not admin(c.from_user.id):return
    admin_panel(c.message.chat.id)

@bot.callback_query_handler(func=lambda c:c.data=='stats')
def stats(c):
    if not admin(c.from_user.id):return
    s=db.stats();bot.send_message(c.message.chat.id,f"<b>داشبورد SonarX</b>\nکاربران: {s['users']}\nسفارش موفق: {s['orders']}\nفروش ثبت‌شده: {fmt(s['revenue'])}\nرسیدهای معلق: {s['pending_deposits']}\nتیکت‌های باز: {s['open_tickets']}")

@bot.callback_query_handler(func=lambda c:c.data=='admin_config')
def admin_config(c):
    if not admin(c.from_user.id):return
    k=types.InlineKeyboardMarkup(row_width=2)
    for key,label in [('shop_enabled','فروشگاه'),('deposits_enabled','پرداخت و شارژ'),('support_enabled','پشتیبانی'),('maintenance','حالت تعمیرات'),('channel_membership','عضویت اجباری'),('auto_renew','تمدید خودکار'),('traffic_sync','همگام‌سازی مصرف'),('backup','پشتیبان‌گیری'),('rate_limit','محدودیت درخواست'),('anti_fraud','ضدتقلب')]:
        default=feature_default(key);val=db.get_setting('feature.'+key,default)
        k.add(types.InlineKeyboardButton(f"{'🟢' if val=='1' else '🔴'} {label}",callback_data='cfg:'+key))
    for key,label in [('welcome','متن خوش‌آمدگویی'),('payment','راهنمای پرداخت'),('shop_notice','پیام فروشگاه'),('faq','متن سوالات متداول'),('tutorials','متن آموزش'),('rules','قوانین ربات')]:
        k.add(types.InlineKeyboardButton('✏️ '+label,callback_data='edittext:'+key))
    k.add(types.InlineKeyboardButton('تمام قابلیت‌ها',callback_data='feature_matrix:0'))
    k.add(types.InlineKeyboardButton('بازگشت',callback_data='admin'))
    bot.send_message(c.message.chat.id,'تنظیمات عمومی و متن‌های ربات:',reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('cfg:'))
def config_toggle(c):
    if not admin(c.from_user.id):return
    key=c.data.split(':',1)[1];dbkey='feature.'+key;default=feature_default(key)
    new='0' if db.get_setting(dbkey,default)=='1' else '1';db.set_setting(dbkey,new);db.audit(c.from_user.id,'setting.toggle',dbkey,new)
    bot.answer_callback_query(c.id,'تنظیم ذخیره شد.');admin_config(c)

@bot.callback_query_handler(func=lambda c:c.data.startswith('edittext:'))
def edit_text(c):
    if not admin(c.from_user.id):return
    states[c.from_user.id]={'step':'set_text','key':c.data.split(':',1)[1]};bot.send_message(c.message.chat.id,'متن جدید را ارسال کنید؛ /cancel برای لغو.')

@bot.callback_query_handler(func=lambda c:c.data=='admin_products')
def admin_products(c):
    if not admin(c.from_user.id):return
    k=types.InlineKeyboardMarkup()
    for p in db.products_admin():
        k.add(types.InlineKeyboardButton(f"{p['name']} — {'فعال' if p['active'] else 'غیرفعال'}",callback_data=f"ptoggle:{p['id']}"))
        k.add(types.InlineKeyboardButton(f"✏️ تغییر قیمت {p['name']} ({fmt(p['price'])})",callback_data=f"pprice:{p['id']}"))
    bot.send_message(c.message.chat.id,'برای تغییر وضعیت پلن انتخاب کنید:',reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('pprice:'))
def product_price_start(c):
    if not admin(c.from_user.id):return
    pid=int(c.data.split(':')[1]);p=db.product(pid,False)
    if not p:return bot.answer_callback_query(c.id,'پلن پیدا نشد.')
    states[c.from_user.id]={'step':'product_price','product_id':pid}
    bot.send_message(c.message.chat.id,f"قیمت جدید پلن {safe(p['name'])} را به {settings.currency_label} ارسال کنید.")

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
    k.add(types.InlineKeyboardButton('بازگشت',callback_data='admin'))
    bot.send_message(c.message.chat.id,'مدیریت سرورها و پنل‌ها:',reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('server_details:'))
def server_details(c):
    if not admin(c.from_user.id):return
    sid=int(c.data.split(':')[1]);s=next((x for x in db.servers_admin() if x['id']==sid),None)
    if not s:return bot.answer_callback_query(c.id,'سرور پیدا نشد.')
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('🔌 تست اتصال',callback_data=f'server_test:{sid}'),types.InlineKeyboardButton('فعال/غیرفعال',callback_data=f'stoggle:{sid}'))
    kb.add(types.InlineKeyboardButton('بازگشت',callback_data='admin_servers'))
    bot.send_message(c.message.chat.id,f"<b>{safe(s['name'])}</b>\nنوع پنل: {safe(s['panel_type'])}\nآدرس: {safe(s['url'])}\nاولویت: {s['priority']}\nوضعیت: {'فعال' if s['active'] else 'غیرفعال'}\nاطلاعات ورود محرمانه است و نمایش داده نمی‌شود.",reply_markup=kb)

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
    states[c.from_user.id]={'step':c.data}
    prompts={'add_product':'نام پلن را ارسال کنید. مراحل بعد: قیمت، حجم GB، روز، توضیح، Group IDs به‌شکل [1,2].','add_server':'نوع پنل را وارد کنید: pasarguard (پیشنهادی)، marzban یا marzneshin. سپس نام سرور، URL، نام کاربری و رمز API را ارسال کنید.','credit':'شناسه کاربر را ارسال کنید؛ سپس مبلغ مثبت.','user_block':'شناسه عددی کاربر را ارسال کنید تا وضعیت مسدودسازی تغییر کند.','broadcast':'متن پیام همگانی را ارسال کنید.','admin_add':'شناسه عددی مدیر جدید را ارسال کنید.','admin_remove':'شناسه عددی مدیر را ارسال کنید.'}
    bot.send_message(c.message.chat.id,prompts[c.data]+' /cancel برای لغو.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step') in {'add_product','add_server','credit','user_block','broadcast','admin_add','admin_remove','set_text','ticket_reply','product_price'})
def admin_input(m):
    if not admin(m.from_user.id):return
    st=states[m.from_user.id];step=st['step'];value=(m.text or '').strip()
    if step=='set_text':
        db.set_setting('text.'+st['key'],value);db.audit(m.from_user.id,'setting.text',st['key'],'updated');states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'متن ذخیره شد.')
    if step=='broadcast':
        states.pop(m.from_user.id,None);sent=failed=0
        for uid in db.users_for_broadcast():
            try:bot.send_message(uid,safe(value));sent+=1
            except Exception:failed+=1
            time.sleep(.05)
        db.audit(m.from_user.id,'broadcast',None,f'sent={sent};failed={failed}');return bot.send_message(m.chat.id,f'پایان ارسال؛ موفق {sent}، ناموفق {failed}.')
    if step in {'user_block','admin_add','admin_remove'}:
        try:uid=int(value)
        except ValueError:return bot.send_message(m.chat.id,'شناسه باید عددی باشد.')
        if step=='user_block':
            u=db.get_user(uid)
            if not u:return bot.send_message(m.chat.id,'کاربر پیدا نشد.')
            db.set_user_blocked(uid,not bool(u['is_blocked']));db.audit(m.from_user.id,'user.block',str(uid),str(not bool(u['is_blocked'])));states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'وضعیت کاربر تغییر کرد.')
        if step=='admin_add':db.add_admin(uid);db.audit(m.from_user.id,'admin.add',str(uid))
        else:
            if uid in settings.admin_ids:return bot.send_message(m.chat.id,'مدیر اصلی از طریق ADMIN_IDS حذف می‌شود.')
            db.remove_admin(uid);db.audit(m.from_user.id,'admin.remove',str(uid))
        states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'دسترسی مدیر به‌روزرسانی شد.')
    if step=='ticket_reply':
        tid=st['ticket_id'];ticket=db.reply_ticket(tid,m.from_user.id,value);bot.send_message(ticket['user_id'],f'پاسخ پشتیبانی به تیکت #{tid}:\n{safe(value)}');states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'پاسخ ارسال شد.')
    if step=='product_price':
        try:price=int(value.replace(',',''))
        except ValueError:return bot.send_message(m.chat.id,'قیمت باید عددی باشد.')
        if price<0:return bot.send_message(m.chat.id,'قیمت نمی‌تواند منفی باشد.')
        db.update_product_price(st['product_id'],price);db.audit(m.from_user.id,'product.price',str(st['product_id']),str(price));states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'قیمت پلن به‌روزرسانی شد.')
    vals=st.setdefault('values',[]);vals.append(value)
    required={'add_product':6,'add_server':5,'credit':2}[step]
    if len(vals)<required:
        labels={'add_product':['نام پلن','قیمت','حجم GB','مدت اعتبار به روز (0 نامحدود)','توضیحات','Group IDs به‌شکل JSON مثل [1,2]'],'add_server':['نوع پنل: pasarguard / marzban / marzneshin','نام سرور','URL پنل با https://','نام کاربری API','رمز API'],'credit':['شناسه کاربر','مبلغ افزایش موجودی']}
        bot.send_message(m.chat.id,f"مرحله {len(vals)+1}/{required}: {labels[step][len(vals)]}");return
    states.pop(m.from_user.id,None)
    try:
        if step=='add_product':
            name,price,gb,days,desc,groups=vals
            if not all(re.fullmatch(r'\d+',x) for x in [price,gb,days]):raise ValueError('قیمت، حجم و روز باید عدد نامنفی باشند.')
            group_ids=json.loads(groups or '[]')
            if not isinstance(group_ids,list) or any(not isinstance(x,int) for x in group_ids):raise ValueError('Group IDs باید آرایه‌ای از اعداد باشد.')
            db.add_product(name,desc,int(price),int(gb),int(days),json.dumps(group_ids));db.audit(m.from_user.id,'product.add',name)
            bot.send_message(m.chat.id,'پلن ایجاد شد.')
        elif step=='add_server':
            panel_type,name,url,user,pw=vals
            panel_type=panel_type.lower()
            if panel_type not in {'pasarguard','marzban','marzneshin'}:raise ValueError('نوع پنل پشتیبانی‌شده: pasarguard، marzban، marzneshin')
            if settings.verify_tls and not url.startswith('https://'):raise ValueError('برای TLS فعال، URL باید https باشد.')
            if not box:raise RuntimeError('FERNET_KEY تنظیم نشده است.')
            client_cls={'pasarguard':PasarGuardClient,'marzban':MarzbanClient,'marzneshin':MarzneshinClient}[panel_type]
            client_cls(url,user,pw,settings.verify_tls).login();db.add_server(name,url,user,box.encrypt(pw),panel_type);db.audit(m.from_user.id,'server.add',name,panel_type)
            bot.send_message(m.chat.id,'اتصال پنل تأیید و سرور ذخیره شد.')
        elif step=='credit':
            uid,amount=map(int,vals)
            if amount<=0:raise ValueError('مبلغ باید مثبت باشد.')
            db.add_credit(uid,amount,f'admin:{m.from_user.id}:{secrets.token_hex(8)}');db.audit(m.from_user.id,'wallet.credit',str(uid),str(amount))
            bot.send_message(m.chat.id,'کیف پول شارژ شد.')
            try:bot.send_message(uid,f'کیف پول شما به مبلغ {fmt(amount)} توسط مدیریت شارژ شد.')
            except Exception:pass
    except Exception as exc:bot.send_message(m.chat.id,'خطا: '+safe(exc))

@bot.callback_query_handler(func=lambda c:c.data=='admin_deposits')
def admin_deposits(c):
    if not admin(c.from_user.id):return
    rows=db.pending_deposits()
    if not rows:return bot.send_message(c.message.chat.id,'رسید معلقی وجود ندارد.')
    for d in rows:
        k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('تأیید',callback_data=f"dep:yes:{d['id']}"),types.InlineKeyboardButton('رد',callback_data=f"dep:no:{d['id']}"))
        try:bot.send_photo(c.message.chat.id,d['receipt_id'],caption=f"رسید #{d['id']} | کاربر {d['user_id']} | {fmt(d['amount'])}",reply_markup=k)
        except Exception:bot.send_message(c.message.chat.id,f"رسید #{d['id']} | کاربر {d['user_id']} | {fmt(d['amount'])}",reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data=='admin_users')
def admin_users(c):
    if not admin(c.from_user.id):return
    rows=db.list_users(50)
    if not rows:return bot.send_message(c.message.chat.id,'کاربری ثبت نشده است.')
    lines=[f"{u['id']} | @{u['username'] or '-'} | {fmt(u['balance'])} | {'مسدود' if u['is_blocked'] else 'فعال'}" for u in rows]
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('🔎 جستجوی کاربر',callback_data='user_search'))
    bot.send_message(c.message.chat.id,'<b>۵۰ کاربر اخیر</b>\n'+safe('\n'.join(lines)),reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data=='admin_tickets')
def admin_tickets(c):
    if not admin(c.from_user.id):return
    rows=db.open_tickets();k=types.InlineKeyboardMarkup()
    for t in rows:k.add(types.InlineKeyboardButton(f"#{t['id']} — {t['subject']}",callback_data=f"ticket:{t['id']}"))
    bot.send_message(c.message.chat.id,'تیکت‌های باز:' if rows else 'تیکت بازی وجود ندارد.',reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('ticket:'))
def ticket_open(c):
    if not admin(c.from_user.id):return
    tid=int(c.data.split(':')[1]);msgs=db.ticket_messages(tid);body='\n'.join(f"{x['sender_id']}: {x['body']}" for x in msgs)
    states[c.from_user.id]={'step':'ticket_reply','ticket_id':tid}
    k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('بستن تیکت',callback_data=f'ticketclose:{tid}'))
    bot.send_message(c.message.chat.id,f'<b>تیکت #{tid}</b>\n{safe(body)}\n\nبرای پاسخ، متن را ارسال کنید.',reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('ticketclose:'))
def ticket_close(c):
    if not admin(c.from_user.id):return
    tid=int(c.data.split(':')[1]);db.close_ticket(tid);db.audit(c.from_user.id,'ticket.close',str(tid));bot.answer_callback_query(c.id,'تیکت بسته شد.')

@bot.callback_query_handler(func=lambda c:c.data=='admin_admins')
def admin_admins(c):
    if not admin(c.from_user.id):return
    rows=db.list_admins();txt='\n'.join(f"{x['user_id']} — {x['role']} — {'فعال' if x['active'] else 'غیرفعال'}" for x in rows) or 'مدیر دیگری ثبت نشده است.'
    k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('➕ افزودن مدیر',callback_data='admin_add'),types.InlineKeyboardButton('➖ حذف مدیر',callback_data='admin_remove'))
    bot.send_message(c.message.chat.id,'<b>مدیران</b>\n'+safe(txt),reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data=='admin_reviews')
def admin_reviews(c):
    if not admin(c.from_user.id):return
    rows=db.review_orders()
    if not rows:return bot.send_message(c.message.chat.id,'سفارش نیازمند بررسی وجود ندارد.')
    for o in rows:
        k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('بازپرداخت (پس از بررسی پنل)',callback_data=f"reviewrefund:{o['id']}"))
        bot.send_message(c.message.chat.id,f"<b>سفارش {o['id']}</b>\nکاربر: {o['user_id']}\nمبلغ: {fmt(o['amount'])}\nنام پنل: {safe(o['remote_username'] or '-')}\nخطا: {safe(o['error'] or '')}",reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('reviewrefund:'))
def review_refund(c):
    if not admin(c.from_user.id):return
    oid=c.data.split(':',1)[1];o=db.refund_review_order(oid)
    if not o:return bot.answer_callback_query(c.id,'سفارش قبلاً تعیین تکلیف شده است.',show_alert=True)
    db.audit(c.from_user.id,'order.review_refund',oid);bot.answer_callback_query(c.id,'بازپرداخت انجام شد.')
    try:bot.send_message(o['user_id'],f'مبلغ سفارش بررسی‌شده ({fmt(o["amount"])}) به کیف پول برگشت داده شد.')
    except Exception:pass

@bot.message_handler(commands=['admin'])
def admin_command(m):
    if admin(m.from_user.id):admin_panel(m.chat.id)


# ---------------- SonarX 6.0: hybrid navigation and advanced admin ----------------
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
  ("usd_currency","ارز دلاری"),("wallet_hold","موجودی معلق"),("reseller","سیستم نمایندگی"),
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

IMPLEMENTED_FEATURES={'shop_enabled','deposits_enabled','support_enabled','manual_receipt','discounts','gift_codes','faq','tutorials','audit_log','referral','dice_game','lottery'}
def feature_default(key):
    return '1' if key in IMPLEMENTED_FEATURES else '0'

def _feature_page(chat_id, page=0, message_id=None):
    groups=FEATURE_GROUPS
    flat=[(g,k,l) for g,items in groups for k,l in items]
    page_size=14; pages=(len(flat)+page_size-1)//page_size
    page=max(0,min(page,pages-1)); chunk=flat[page*page_size:(page+1)*page_size]
    kb=types.InlineKeyboardMarkup(row_width=2)
    current_group=None
    for group,key,label in chunk:
        state=db.get_setting('feature.'+key, feature_default(key))
        kb.add(types.InlineKeyboardButton(f"{'🟢' if state=='1' else '🔴'} {label}",callback_data=f"fx:{key}:{page}"))
    nav=[]
    if page: nav.append(types.InlineKeyboardButton('صفحه قبل ◀',callback_data=f'feature_matrix:{page-1}'))
    if page<pages-1: nav.append(types.InlineKeyboardButton('صفحه بعد ▶',callback_data=f'feature_matrix:{page+1}'))
    if nav: kb.row(*nav)
    kb.add(types.InlineKeyboardButton('بازگشت به مدیریت',callback_data='admin'))
    txt=f"<b>قابلیت‌های SonarX</b>\nصفحه {page+1} از {pages}\nوضعیت هر قابلیت با انتخاب دکمه تغییر می‌کند."
    if message_id:
        try: bot.edit_message_text(txt,chat_id,message_id,reply_markup=kb)
        except Exception: bot.send_message(chat_id,txt,reply_markup=kb)
    else: bot.send_message(chat_id,txt,reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data.startswith('feature_matrix:'))
def feature_matrix(c):
    if not admin(c.from_user.id): return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    _feature_page(c.message.chat.id,int(c.data.split(':')[1]),c.message.message_id)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c:c.data.startswith('fx:'))
def feature_toggle(c):
    if not admin(c.from_user.id): return bot.answer_callback_query(c.id,'دسترسی ندارید.',show_alert=True)
    _,key,page=c.data.split(':')
    old=db.get_setting('feature.'+key,feature_default(key))
    new='0' if old=='1' else '1'
    db.set_setting('feature.'+key,new);db.audit(c.from_user.id,'feature.toggle',key,new)
    bot.answer_callback_query(c.id,'تنظیم ذخیره شد.')
    _feature_page(c.message.chat.id,int(page),c.message.message_id)

@bot.message_handler(func=lambda m:m.text in {'🛒 خرید سرویس','👤 حساب من','📦 سرویس‌های من','💳 افزایش موجودی','🎫 پشتیبانی','🎟 تیکت‌های من','🎁 کد هدیه','🎲 شانس و جایزه','📚 آموزش اتصال','❓ سوالات متداول','⚙️ پنل مدیریت'})
def reply_menu_router(m):
    t=m.text
    if t=='🛒 خرید سرویس': return bot.send_message(m.chat.id,'فروشگاه:',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('نمایش پلن‌ها',callback_data='shop')))
    if t=='👤 حساب من':
        u=db.get_user(m.from_user.id)
        if u:return bot.send_message(m.chat.id,f"<b>حساب کاربری</b>\nشناسه: <code>{u['id']}</code>\nموجودی: <b>{fmt(u['balance'])}</b>",reply_markup=home(m.from_user.id))
    if t=='📦 سرویس‌های من': return bot.send_message(m.chat.id,'سرویس‌های شما:',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('مشاهده سرویس‌ها',callback_data='orders')))
    if t=='💳 افزایش موجودی': return bot.send_message(m.chat.id,'افزایش موجودی:',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('ثبت درخواست شارژ',callback_data='deposit')))
    if t=='🎫 پشتیبانی': return bot.send_message(m.chat.id,'پشتیبانی:',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('ثبت تیکت',callback_data='support')))
    if t=='🎟 تیکت‌های من': return bot.send_message(m.chat.id,'تیکت‌های من:',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('نمایش تیکت‌ها',callback_data='my_tickets')))
    if t=='🎲 شانس و جایزه': return bot.send_message(m.chat.id,'بازی تاس:',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('پرتاب تاس',callback_data='dice_game')))
    if t=='🎁 کد هدیه':
        states[m.from_user.id]={'step':'gift_redeem'}
        return bot.send_message(m.chat.id,'کد هدیه را ارسال کنید. /cancel برای لغو.')
    if t=='📚 آموزش اتصال': return bot.send_message(m.chat.id,'آموزش:',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('مشاهده آموزش',callback_data='tutorials')))
    if t=='❓ سوالات متداول': return bot.send_message(m.chat.id,'سوالات متداول:',reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('مشاهده سوالات',callback_data='faq')))
    if t=='⚙️ پنل مدیریت':
        if admin(m.from_user.id): return admin_panel(m.chat.id)

@bot.message_handler(commands=['panel'])
def admin_shortcut(m):
    if admin(m.from_user.id): admin_panel(m.chat.id)
    else: bot.reply_to(m,'دسترسی ندارید.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='gift_redeem')
def redeem_gift_message(m):
    if not feature('gift_codes','1'):
        states.pop(m.from_user.id,None)
        return bot.send_message(m.chat.id,'کد هدیه غیرفعال است.')
    code=(m.text or '').strip().upper()
    try:
        g=db.redeem_gift(code,m.from_user.id)
        if not g: return bot.send_message(m.chat.id,'کد نامعتبر، غیرفعال یا قبلاً مصرف شده است.')
        states.pop(m.from_user.id,None)
        bot.send_message(m.chat.id,f"کد هدیه ثبت شد. اعتبار کیف پول: {fmt(g['amount'])}",reply_markup=home(m.from_user.id))
    except Exception as e: bot.send_message(m.chat.id,'خطا در ثبت کد: '+safe(e))

@bot.callback_query_handler(func=lambda c:c.data=='redeem_gift')
def redeem_gift_start(c):
    if not feature('gift_codes','1'):return bot.answer_callback_query(c.id,'کد هدیه غیرفعال است.',show_alert=True)
    states[c.from_user.id]={'step':'gift_redeem'}
    bot.send_message(c.message.chat.id,'کد هدیه را ارسال کنید. /cancel برای لغو.')

@bot.callback_query_handler(func=lambda c:c.data=='tutorials')
def tutorials(c):
    txt=text_setting('tutorials','آموزش اتصال: لینک اشتراک را در برنامه سازگار با پروتکل سرویس وارد کنید.')
    bot.send_message(c.message.chat.id,safe(txt),reply_markup=home(c.from_user.id))

@bot.callback_query_handler(func=lambda c:c.data=='faq')
def faq(c):
    txt=text_setting('faq','سوالات متداول:\n۱) لینک اشتراک را در کلاینت وارد کنید.\n۲) در صورت اختلال تیکت ثبت کنید.\n۳) تمدید از بخش مدیریت/پشتیبانی پیگیری می‌شود.')
    bot.send_message(c.message.chat.id,safe(txt),reply_markup=home(c.from_user.id))

@bot.callback_query_handler(func=lambda c:c.data=='user_home')
def user_home(c):
    bot.send_message(c.message.chat.id,'منوی کاربری:',reply_markup=user_reply_keyboard(c.from_user.id))
    bot.send_message(c.message.chat.id,'انتخاب کنید:',reply_markup=home(c.from_user.id))

@bot.callback_query_handler(func=lambda c:c.data=='referral')
def referral(c):
    if not feature('referral','0'):return bot.answer_callback_query(c.id,'دعوت دوستان غیرفعال است.',show_alert=True)
    me=bot.get_me();stats=db.referral_stats(c.from_user.id)
    link=f"https://t.me/{me.username}?start={c.from_user.id}"
    bot.send_message(c.message.chat.id,f"<b>دعوت دوستان</b>\nلینک اختصاصی شما:\n<code>{safe(link)}</code>\nتعداد دعوت موفق: {stats['n']}\nمجموع پاداش ثبت‌شده: {fmt(stats['bonus'])}")

@bot.callback_query_handler(func=lambda c:c.data=='dice_game')
def dice_game(c):
    if not feature('dice_game','0'):return bot.answer_callback_query(c.id,'بازی جایزه فعلاً غیرفعال است.',show_alert=True)
    roll=bot.send_dice(c.message.chat.id,emoji='🎲')
    if roll.dice and roll.dice.value==6:
        reward=int(db.get_setting('dice_reward','0') or 0)
        if reward>0:
            db.add_credit(c.from_user.id,reward,f"dice:{c.from_user.id}:{roll.message_id}",kind='dice_reward')
            bot.send_message(c.message.chat.id,f"🎉 برنده شدید! {fmt(reward)} به کیف پول شما افزوده شد.")
        else:bot.send_message(c.message.chat.id,'عدد ۶ آوردید؛ جایزه هنوز توسط مدیریت تنظیم نشده است.')
    else:bot.send_message(c.message.chat.id,'این بار جایزه‌ای دریافت نکردید. دوباره تلاش کنید.')

@bot.callback_query_handler(func=lambda c:c.data=='my_tickets')
def my_tickets(c):
    rows=db.tickets_for(c.from_user.id)
    if not rows:return bot.send_message(c.message.chat.id,'هنوز تیکتی ثبت نکرده‌اید.')
    txt='\n'.join(f"#{t['id']} | {safe(t['subject'])} | {t['status']}" for t in rows)
    bot.send_message(c.message.chat.id,'<b>تیکت‌های شما</b>\n'+txt)

@bot.callback_query_handler(func=lambda c:c.data=='lottery_join')
def lottery_join(c):
    if not feature('lottery','0'):return bot.answer_callback_query(c.id,'قرعه‌کشی فعلاً غیرفعال است.',show_alert=True)
    try:db.join_lottery(c.from_user.id);bot.answer_callback_query(c.id,'ثبت‌نام انجام شد.')
    except Exception:bot.answer_callback_query(c.id,'قبلاً ثبت‌نام کرده‌اید یا خطایی رخ داده است.',show_alert=True)

@bot.callback_query_handler(func=lambda c:c.data=='lottery_admin')
def lottery_admin(c):
    if not admin(c.from_user.id):return
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('انتخاب برنده',callback_data='lottery_draw'))
    bot.send_message(c.message.chat.id,f"شرکت‌کنندگان ثبت‌شده: {db.lottery_count()}",reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data=='addon_prices')
def addon_prices_start(c):
    if not admin(c.from_user.id):return
    states[c.from_user.id]={'step':'addon_prices'}
    gb=db.get_setting('addons.price_per_gb','0');day=db.get_setting('addons.price_per_day','0')
    bot.send_message(c.message.chat.id,f"قیمت فعلی هر GB: {fmt(gb)}\nقیمت فعلی هر روز: {fmt(day)}\nقیمت‌ها را به شکل GB,روز ارسال کنید؛ مثال: 10000,5000")

@bot.callback_query_handler(func=lambda c:c.data=='finance')
def finance(c):
    if not admin(c.from_user.id):return
    st=db.stats();tops=db.top_users(5);buyers=db.top_buyers(5);depositors=db.top_depositors(5)
    txt=f"<b>بخش مالی</b>\nفروش موفق: {fmt(st['revenue'])}\nسفارش‌ها: {st['orders']}\nرسیدهای معلق: {st['pending_deposits']}\n\n<b>بیشترین خرید</b>\n"
    txt+='\n'.join(f"{u['id']} — {fmt(u['purchased'])}" for u in buyers)
    txt+='\n\n<b>بیشترین شارژ</b>\n'+'\n'.join(f"{u['id']} — {fmt(u['deposited'])}" for u in depositors)
    txt+='\n\n<b>بیشترین موجودی کیف پول</b>\n'+'\n'.join(f"{u['id']} — {fmt(u['balance'])}" for u in tops)
    kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('تنظیم راهنمای پرداخت',callback_data='edittext:payment'))
    kb.add(types.InlineKeyboardButton('تنظیم قیمت تمدید/حجم',callback_data='addon_prices'))
    kb.add(types.InlineKeyboardButton('رسیدهای معلق',callback_data='admin_deposits'))
    bot.send_message(c.message.chat.id,safe(txt),reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data=='lottery_draw')
def lottery_draw(c):
    if not admin(c.from_user.id):return
    uid=db.lottery_winner()
    if not uid:return bot.answer_callback_query(c.id,'شرکت‌کننده‌ای ثبت نشده است.',show_alert=True)
    db.audit(c.from_user.id,'lottery.draw',str(uid))
    bot.send_message(c.message.chat.id,f'برنده قرعه‌کشی: <code>{uid}</code>')

@bot.callback_query_handler(func=lambda c:c.data=='channel_post')
def channel_post_start(c):
    if not admin(c.from_user.id):return
    states[c.from_user.id]={'step':'channel_post'}
    bot.send_message(c.message.chat.id,'متن پست کانال را ارسال کنید. کانال مقصد باید با kind=announcement در تنظیمات کانال ثبت شده باشد.')

@bot.callback_query_handler(func=lambda c:c.data=='categories')
def categories_admin(c):
    if not admin(c.from_user.id): return
    rows=db.categories();kb=types.InlineKeyboardMarkup(row_width=2)
    for x in rows: kb.add(types.InlineKeyboardButton('🗂 '+x['name'],callback_data=f'catdel:{x["id"]}'))
    kb.add(types.InlineKeyboardButton('➕ افزودن دسته',callback_data='catadd'))
    kb.add(types.InlineKeyboardButton('بازگشت',callback_data='admin'))
    bot.send_message(c.message.chat.id,'دسته‌ها (برای حذف انتخاب کنید):',reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data=='catadd')
def category_add(c):
    if not admin(c.from_user.id): return
    states[c.from_user.id]={'step':'category_add'};bot.send_message(c.message.chat.id,'نام دسته جدید را ارسال کنید.')

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
    bot.send_message(c.message.chat.id,'مدیریت کدهای تخفیف:',reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data=='discount_add')
def discount_add(c):
    if not admin(c.from_user.id): return
    states[c.from_user.id]={'step':'discount_add'}
    bot.send_message(c.message.chat.id,'کد، درصد، سقف استفاده را با کاما بفرستید. نمونه: SONAR20,20,100')

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
    kb.add(types.InlineKeyboardButton('بازگشت',callback_data='admin'))
    kb.add(types.InlineKeyboardButton('🎲 انتخاب برنده قرعه‌کشی',callback_data='lottery_draw'))
    txt='کدهای هدیه:\n'+'\n'.join(f"{x['code']} | مبلغ {fmt(x['amount'])} | مصرف {x['used_count']}/{x['usage_limit']}" for x in rows)
    bot.send_message(c.message.chat.id,safe(txt),reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data=='gift_add')
def gift_add(c):
    if not admin(c.from_user.id): return
    states[c.from_user.id]={'step':'gift_add'}
    bot.send_message(c.message.chat.id,'کد، مبلغ اعتبار کیف پول و سقف مصرف را با کاما ارسال کنید.\nنمونه: WELCOME50,50000,100')

@bot.callback_query_handler(func=lambda c:c.data=='resellers')
def resellers_admin(c):
    if not admin(c.from_user.id): return
    rows=db.resellers();kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('➕ افزودن/ویرایش نماینده',callback_data='reseller_add'))
    txt='نمایندگان:\n'+'\n'.join(f"{x['user_id']} — {x['commission_percent']}% — {'فعال' if x['active'] else 'غیرفعال'}" for x in rows) or 'هنوز نماینده‌ای ثبت نشده است.'
    bot.send_message(c.message.chat.id,safe(txt),reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data=='reseller_add')
def reseller_add(c):
    if not admin(c.from_user.id): return
    states[c.from_user.id]={'step':'reseller_add'}
    bot.send_message(c.message.chat.id,'شناسه عددی نماینده و درصد کمیسیون را با کاما ارسال کنید. نمونه: 123456789,10')

@bot.callback_query_handler(func=lambda c:c.data=='channels')
def channels_admin(c):
    if not admin(c.from_user.id): return
    rows=db.channels();kb=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('➕ تنظیم کانال/گروه',callback_data='channel_add'))
    txt='کانال‌ها و گروه‌ها:\n'+'\n'.join(f"{x['kind']}: {x['chat_id']}" for x in rows) or 'تنظیمی ثبت نشده است.'
    bot.send_message(c.message.chat.id,safe(txt),reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data=='channel_add')
def channel_add(c):
    if not admin(c.from_user.id): return
    states[c.from_user.id]={'step':'channel_add'}
    bot.send_message(c.message.chat.id,'نوع و شناسه کانال را با کاما ارسال کنید. نمونه: required,@channel')

@bot.callback_query_handler(func=lambda c:c.data=='button_manager')
def button_manager(c):
    if not admin(c.from_user.id): return
    kb=types.InlineKeyboardMarkup(row_width=1)
    keys=[('shop','خرید سرویس'),('profile','مشخصات کاربری'),('orders','سرویس‌های من'),('deposit','شارژ کیف پول'),('support','تیکت پشتیبانی'),('gift','کد هدیه'),('tutorials','آموزش'),('faq','سوالات متداول')]
    for key,label in keys:
        state=db.get_setting('button.'+key+'.enabled','1')
        kb.add(types.InlineKeyboardButton(f"{'🟢' if state=='1' else '🔴'} {label}",callback_data=f'button_toggle:{key}'))
        kb.add(types.InlineKeyboardButton('✏️ ویرایش متن '+label,callback_data=f'button_edit:{key}'))
    try:
        custom=json.loads(db.get_setting('custom_buttons','[]'))
        for i,item in enumerate(custom[:8]):
            kb.add(types.InlineKeyboardButton('🗑 حذف '+item.get('label','دکمه')[:30],callback_data=f'button_delete:{i}'))
    except Exception: pass
    kb.add(types.InlineKeyboardButton('➕ افزودن دکمه لینک‌دار',callback_data='button_add'))
    bot.send_message(c.message.chat.id,'وضعیت و متن دکمه‌ها؛ لینک‌های سفارشی نیز قابل افزودن هستند.',reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data.startswith('button_toggle:'))
def button_toggle(c):
    if not admin(c.from_user.id): return
    key=c.data.split(':',1)[1];old=db.get_setting('button.'+key+'.enabled','1');db.set_setting('button.'+key+'.enabled','0' if old=='1' else '1')
    db.audit(c.from_user.id,'button.toggle',key,'1' if old=='0' else '0');bot.answer_callback_query(c.id,'ذخیره شد.');button_manager(c)

@bot.callback_query_handler(func=lambda c:c.data=='bulk_accounts')
def bulk_accounts(c):
    if not admin(c.from_user.id): return
    states[c.from_user.id]={'step':'bulk_accounts'}
    bot.send_message(c.message.chat.id,'برای ساخت گروهی، مشخصات را ارسال کنید:\nتعداد,حجمGB,روز,GroupIDs(JSON)\nمثال: 5,10,30,[1,2]\nاین نسخه فقط Job را ثبت می‌کند؛ ایجاد واقعی دسته‌ای روی پنل تا قبل از بررسی هر حساب اجرا نمی‌شود.')

@bot.callback_query_handler(func=lambda c:c.data.startswith('button_edit:'))
def button_edit(c):
    if not admin(c.from_user.id): return
    key=c.data.split(':',1)[1];states[c.from_user.id]={'step':'button_edit','key':key}
    bot.send_message(c.message.chat.id,'متن جدید دکمه را ارسال کنید.')

@bot.callback_query_handler(func=lambda c:c.data.startswith('button_delete:'))
def button_delete(c):
    if not admin(c.from_user.id): return
    i=int(c.data.split(':')[1]);custom=json.loads(db.get_setting('custom_buttons','[]'))
    if 0<=i<len(custom):
        removed=custom.pop(i);db.set_setting('custom_buttons',json.dumps(custom,ensure_ascii=False));db.audit(c.from_user.id,'button.custom_delete',removed.get('label',''))
    bot.answer_callback_query(c.id,'دکمه حذف شد.');button_manager(c)

@bot.callback_query_handler(func=lambda c:c.data=='button_add')
def button_add(c):
    if not admin(c.from_user.id): return
    states[c.from_user.id]={'step':'button_add'}
    bot.send_message(c.message.chat.id,'عنوان و URL را با کاما ارسال کنید. فقط HTTPS. مثال: پنل وب,https://example.com')

@bot.callback_query_handler(func=lambda c:c.data=='reports')
def reports(c):
    if not admin(c.from_user.id): return
    st=db.stats();top=db.top_users(5);buyers=db.top_buyers(5);depositors=db.top_depositors(5);active=db.top_active_services(5);aud=db.recent_audit(15)
    txt=f"<b>گزارش SonarX</b>\nکاربران: {st['users']} | سفارش موفق: {st['orders']}\nفروش: {fmt(st['revenue'])}\nرسید معلق: {st['pending_deposits']} | تیکت باز: {st['open_tickets']}\n\n<b>بیشترین خرید</b>\n"
    txt+='\n'.join(f"{x['id']} @{x['username'] or '-'} — {fmt(x['purchased'])} ({x['orders']} سفارش)" for x in buyers)
    txt+='\n\n<b>بیشترین شارژ</b>\n'+'\n'.join(f"{x['id']} — {fmt(x['deposited'])}" for x in depositors)
    txt+='\n\n<b>بیشترین سرویس فعال</b>\n'+'\n'.join(f"{x['id']} — {x['active_services']}" for x in active)
    txt+='\n\n<b>بیشترین موجودی کیف پول</b>\n'+'\n'.join(f"{x['id']} — {fmt(x['balance'])}" for x in top)
    txt+='\n\n<b>آخرین رویدادها</b>\n'+'\n'.join(f"{x['created_at']} | {x['actor_id']} | {x['action']}" for x in aud)
    bot.send_message(c.message.chat.id,safe(txt))

@bot.callback_query_handler(func=lambda c:c.data=='audit')
def audit(c):
    if not admin(c.from_user.id): return
    rows=db.recent_audit(40)
    txt='\n'.join(f"{x['id']} | {x['created_at']} | {x['actor_id']} | {x['action']} | {x['target'] or '-'}" for x in rows)
    bot.send_message(c.message.chat.id,'<b>لاگ مدیریتی</b>\n'+safe(txt or 'لاگی وجود ندارد.'))

@bot.callback_query_handler(func=lambda c:c.data=='user_search')
def user_search_start(c):
    if not admin(c.from_user.id): return
    states[c.from_user.id]={'step':'user_search'}
    bot.send_message(c.message.chat.id,'شناسه عددی، نام یا username کاربر را وارد کنید.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step')=='user_search')
def user_search_result(m):
    if not admin(m.from_user.id): return
    q=(m.text or '').strip();rows=db.search_users(q,25);states.pop(m.from_user.id,None)
    if not rows:return bot.send_message(m.chat.id,'کاربری یافت نشد.')
    kb=types.InlineKeyboardMarkup(row_width=1)
    for u in rows:
        kb.add(types.InlineKeyboardButton(f"{u['id']} | @{u['username'] or '-'} | {fmt(u['balance'])}",callback_data=f"user_detail:{u['id']}"))
    bot.send_message(m.chat.id,'نتایج جستجو:',reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data.startswith('user_detail:'))
def user_detail(c):
    if not admin(c.from_user.id): return
    uid=int(c.data.split(':')[1]);u=db.get_user(uid)
    if not u:return bot.answer_callback_query(c.id,'کاربر پیدا نشد.')
    kb=types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton('مسدود/آزاد',callback_data=f'user_block_toggle:{uid}'))
    kb.add(types.InlineKeyboardButton('شارژ کیف پول',callback_data=f'user_credit_start:{uid}'))
    kb.add(types.InlineKeyboardButton('✉️ پیام خصوصی',callback_data=f'user_message_start:{uid}'))
    bot.send_message(c.message.chat.id,f"<b>کاربر {uid}</b>\nنام: {safe(u['first_name'] or '-')}\nنام کاربری: @{safe(u['username'] or '-')}\nموجودی: {fmt(u['balance'])}\nوضعیت: {'مسدود' if u['is_blocked'] else 'فعال'}",reply_markup=kb)

@bot.callback_query_handler(func=lambda c:c.data.startswith('user_message_start:'))
def user_message_start(c):
    if not admin(c.from_user.id): return
    uid=int(c.data.split(':')[1]);states[c.from_user.id]={'step':'user_message','target':uid}
    bot.send_message(c.message.chat.id,'متن پیام خصوصی را ارسال کنید. /cancel برای لغو.')

@bot.callback_query_handler(func=lambda c:c.data.startswith('user_block_toggle:'))
def user_block_toggle(c):
    if not admin(c.from_user.id): return
    uid=int(c.data.split(':')[1]);u=db.get_user(uid)
    if u: db.set_user_blocked(uid,not bool(u['is_blocked']));db.audit(c.from_user.id,'user.block',str(uid),str(not bool(u['is_blocked'])))
    bot.answer_callback_query(c.id,'وضعیت تغییر کرد.')

@bot.callback_query_handler(func=lambda c:c.data.startswith('user_credit_start:'))
def user_credit_start(c):
    if not admin(c.from_user.id): return
    uid=int(c.data.split(':')[1]);states[c.from_user.id]={'step':'credit','values':[str(uid)]}
    bot.send_message(c.message.chat.id,'مبلغ افزایش موجودی را به تومان وارد کنید.')

@bot.message_handler(func=lambda m:states.get(m.from_user.id,{}).get('step') in {'category_add','discount_add','gift_add','reseller_add','channel_add','bulk_accounts','button_edit','button_add','channel_post','user_message','addon_prices'})
def advanced_admin_input(m):
    if not admin(m.from_user.id): return
    st=states.get(m.from_user.id,{});step=st.get('step');value=(m.text or '').strip()
    try:
        if step=='addon_prices':
            gb,day=[int(x.strip()) for x in value.split(',')]
            if gb<0 or day<0: raise ValueError('قیمت‌ها نمی‌توانند منفی باشند.')
            db.set_setting('addons.price_per_gb',str(gb));db.set_setting('addons.price_per_day',str(day))
            db.audit(m.from_user.id,'addons.prices',None,f'gb={gb};day={day}')
            states.pop(m.from_user.id,None)
            return bot.send_message(m.chat.id,'قیمت‌های افزونه ذخیره شد.')
        if step=='user_message':
            target=st['target'];bot.send_message(target,safe(value))
            db.audit(m.from_user.id,'user.private_message',str(target),'sent')
            states.pop(m.from_user.id,None)
            return bot.send_message(m.chat.id,'پیام ارسال شد.')
        if step=='channel_post':
            dest=next((x['chat_id'] for x in db.channels() if x['kind']=='announcement'),None)
            if not dest: raise ValueError('ابتدا مقصد با kind=announcement در مدیریت کانال‌ها تنظیم شود.')
            bot.send_message(dest,value)
            db.audit(m.from_user.id,'channel.post',dest,'sent')
            states.pop(m.from_user.id,None)
            return bot.send_message(m.chat.id,'پست ارسال شد.')
        if step=='button_edit':
            db.set_setting('button.'+st['key']+'.label',value[:60]);db.audit(m.from_user.id,'button.label',st['key']);states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'متن دکمه ذخیره شد.')
        if step=='button_add':
            label,url=[x.strip() for x in value.split(',',1)]
            if not url.startswith('https://'): raise ValueError('URL باید با https:// شروع شود.')
            custom=json.loads(db.get_setting('custom_buttons','[]'));custom.append({'label':label[:50],'url':url})
            db.set_setting('custom_buttons',json.dumps(custom,ensure_ascii=False));db.audit(m.from_user.id,'button.custom_add',label,url);states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'دکمه لینک‌دار افزوده شد.')
        if step=='category_add':
            if not value or len(value)>80: raise ValueError('نام دسته باید بین ۱ تا ۸۰ نویسه باشد.')
            db.add_category(value);db.audit(m.from_user.id,'category.add',value);states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'دسته ایجاد شد.')
        if step=='discount_add':
            code,percent,limit=[x.strip() for x in value.split(',')]
            percent=int(percent);limit=int(limit)
            if not 1<=percent<=100 or limit<0: raise ValueError('درصد باید ۱ تا ۱۰۰ و سقف استفاده نامنفی باشد.')
            db.add_discount(code,percent,limit);db.audit(m.from_user.id,'discount.add',code);states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'کد تخفیف ایجاد شد.')
        if step=='gift_add':
            code,amount,limit=[x.strip() for x in value.split(',')]
            amount=int(amount);limit=int(limit)
            if not code or amount<=0 or limit<1: raise ValueError('کد غیرخالی، مبلغ مثبت و سقف مصرف حداقل ۱ لازم است.')
            db.add_gift_code(code,amount,0,0,limit);db.audit(m.from_user.id,'gift.add',code);states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'کد هدیه اعتباری ایجاد شد.')
        if step=='reseller_add':
            uid,commission=[int(x.strip()) for x in value.split(',')]
            if not 0<=commission<=100: raise ValueError('کمیسیون باید بین ۰ تا ۱۰۰ باشد.')
            db.set_reseller(uid,commission,True);db.audit(m.from_user.id,'reseller.set',str(uid),commission);states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'نماینده ذخیره شد.')
        if step=='channel_add':
            kind,chat=[x.strip() for x in value.split(',',1)]
            db.save_channel(kind,chat);db.audit(m.from_user.id,'channel.set',kind,chat);states.pop(m.from_user.id,None);return bot.send_message(m.chat.id,'کانال/گروه ذخیره شد.')
        if step=='bulk_accounts':
            n,gb,days,groups=value.split(',',3);n=int(n);gb=int(gb);days=int(days);json.loads(groups)
            if not 1<=n<=50 or gb<0 or days<0: raise ValueError('تعداد ۱ تا ۵۰ و حجم/روز نامنفی باشد.')
            states.pop(m.from_user.id,None)
            return bot.send_message(m.chat.id,'درخواست گروهی دریافت شد. برای جلوگیری از ایجاد اکانت ناخواسته، اجرای واقعی آن نیازمند انتخاب سرور و تأیید نهایی در پنل است؛ این عملیات هنوز به Job اجرایی متصل نشده است.')
    except Exception as e:
        return bot.send_message(m.chat.id,'ورودی نامعتبر: '+safe(e))

@bot.message_handler(commands=['help'])
def help_command(m):
    bot.send_message(m.chat.id,'دستورات:\n/start منوی کاربر\n/panel پنل مدیریت\n/cancel لغو عملیات\nبرای مدیریت سرور از پنل داخل ربات استفاده کنید.',reply_markup=user_reply_keyboard(m.from_user.id))

def main():
    log.info('SonarX starting; backend=%s',db.backend)
    bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
if __name__=='__main__':main()
