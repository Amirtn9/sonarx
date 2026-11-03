import json, logging, secrets, re, time
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
def home(uid):
    k=types.InlineKeyboardMarkup(row_width=2)
    k.add(types.InlineKeyboardButton('🛒 خرید سرویس',callback_data='shop'),types.InlineKeyboardButton('👤 حساب من',callback_data='profile'))
    k.add(types.InlineKeyboardButton('📦 سرویس‌های من',callback_data='orders'),types.InlineKeyboardButton('💳 افزایش موجودی',callback_data='deposit'))
    k.add(types.InlineKeyboardButton('🎫 پشتیبانی',callback_data='support'))
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
    buttons=[('📊 داشبورد','stats'),('🧩 تنظیمات','admin_config'),('📦 پلن‌ها','admin_products'),('🖥 سرورها','admin_servers'),('🧾 رسیدهای معلق','admin_deposits'),('👥 کاربران','admin_users'),
      ('➕ پلن جدید','add_product'),('➕ سرور جدید','add_server'),('💰 شارژ کاربر','credit'),('⛔ مسدودسازی','user_block'),
      ('📢 پیام همگانی','broadcast'),('🎫 تیکت‌ها','admin_tickets'),('🛡 مدیران','admin_admins'),('⚠️ سفارش‌های بررسی','admin_reviews')]
    for label,cb in buttons:k.add(types.InlineKeyboardButton(label,callback_data=cb))
    bot.send_message(chat_id,'<b>مرکز مدیریت SonarX</b>\nمدیریت فروش، مالی، کاربران، پنل‌ها و پشتیبانی.',reply_markup=k)

@bot.message_handler(commands=['start'])
def start(m):
    u=db.user(m.from_user.id,m.from_user.username,m.from_user.first_name)
    if u.get('is_blocked') and not admin(m.from_user.id):return bot.send_message(m.chat.id,'حساب کاربری شما مسدود است.')
    welcome=text_setting('welcome','<b>به SonarX خوش آمدید</b>\nفروشگاه هوشمند سرویس‌های VPN؛ انتخاب پلن، پرداخت و تحویل خودکار.')
    bot.send_message(m.chat.id,welcome,reply_markup=home(m.from_user.id))

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
    k=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton('تأیید خرید',callback_data=f"confirm:{p['id']}"),types.InlineKeyboardButton('بازگشت','shop'))
    bot.edit_message_text(f"<b>{safe(p['name'])}</b>\n{safe(p['description'])}\nحجم: {p['traffic_gb']} GB | اعتبار: {p['days'] or 'نامحدود'} روز\nقیمت: <b>{fmt(p['price'])}</b>",c.message.chat.id,c.message.message_id,reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('confirm:'))
def confirm(c):
    if maintenance(c.from_user.id):return bot.answer_callback_query(c.id,'ربات در حال تعمیرات است.',show_alert=True)
    p=db.product(int(c.data.split(':')[1]))
    if not p:return bot.answer_callback_query(c.id,'پلن پیدا نشد.',show_alert=True)
    try:oid=db.begin_order(c.from_user.id,p)
    except Exception as exc:return bot.answer_callback_query(c.id,str(exc),show_alert=True)
    bot.answer_callback_query(c.id,'سفارش ثبت شد؛ در حال ساخت سرویس…')
    bot.send_message(c.message.chat.id,'⏳ سفارش شما در حال پردازش است.')
    servers=db.servers()
    if not servers:
        db.fail_order_refund(oid,'No active PasarGuard server');return bot.send_message(c.message.chat.id,'سرور فعالی تنظیم نشده است؛ مبلغ به کیف پول برگشت خورد.',reply_markup=home(c.from_user.id))
    # Authenticate candidate servers first. Once a create POST is attempted, do not fail over blindly.
    selected=None;errors=[]
    for server in servers:
        try:
            client=server_client(server);client.login();selected=(server,client);break
        except Exception as exc:errors.append(f"{server['name']}: {exc}");log.warning('Panel login failed: %s',exc)
    if not selected:
        db.fail_order_refund(oid,'Panel login failed: '+'; '.join(errors));return bot.send_message(c.message.chat.id,'اتصال به هیچ سرور فعالی موفق نبود؛ مبلغ برگشت داده شد.',reply_markup=home(c.from_user.id))
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
            db.fail_order_refund(oid,str(exc));bot.send_message(c.message.chat.id,'ساخت سرویس ناموفق بود؛ مبلغ به کیف پول برگشت خورد.')
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
            bot.send_message(c.message.chat.id,msg)
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
    k=types.InlineKeyboardMarkup(row_width=1)
    keys={'shop_enabled':'فروشگاه','deposits_enabled':'شارژ کیف پول','support_enabled':'پشتیبانی','maintenance':'حالت تعمیرات'}
    for key,label in keys.items():
        default='0' if key=='maintenance' else '1';val=db.get_setting('feature.'+key,default)
        k.add(types.InlineKeyboardButton(f"{label}: {'روشن' if val=='1' else 'خاموش'}",callback_data='cfg:'+key))
    for key,label in [('welcome','متن خوش‌آمدگویی'),('payment','راهنمای پرداخت'),('shop_notice','پیام فروشگاه')]:k.add(types.InlineKeyboardButton('✏️ '+label,callback_data='edittext:'+key))
    bot.send_message(c.message.chat.id,'تنظیمات زنده ربات؛ تغییرات در دیتابیس ذخیره می‌شوند.',reply_markup=k)

@bot.callback_query_handler(func=lambda c:c.data.startswith('cfg:'))
def config_toggle(c):
    if not admin(c.from_user.id):return
    key=c.data.split(':',1)[1];dbkey='feature.'+key;default='0' if key=='maintenance' else '1'
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
    k=types.InlineKeyboardMarkup()
    for s in db.servers_admin():k.add(types.InlineKeyboardButton(f"{s['name']} — {'فعال' if s['active'] else 'غیرفعال'}",callback_data=f"stoggle:{s['id']}"))
    bot.send_message(c.message.chat.id,'وضعیت سرورها:',reply_markup=k)

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
    bot.send_message(c.message.chat.id,'<b>۵۰ کاربر اخیر</b>\n'+safe('\n'.join(lines)))

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

def main():
    log.info('SonarX starting; backend=%s',db.backend)
    bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
if __name__=='__main__':main()
