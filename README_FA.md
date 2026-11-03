# SonarX 5.2 — فروشگاه و مدیریت ربات فروش VPN

SonarX یک ربات مستقل تلگرام برای فروش و مدیریت سرویس VPN است. ساختار جریان خرید و بخش‌های مدیریتی با الهام از پروژه Mirza Bot ارائه‌شده طراحی شده، اما برند و مسیر توسعه مستقل SonarX حفظ شده است. **پنل اصلی و اولویت‌دار PasarGuard است.** Adapterهای ثانویه Marzban و Marzneshin نیز اضافه شده‌اند؛ اتصال Sanaei/X-UI موجود در سورس قدیمی در این نسخه ادغام نشده است.

> شفافیت محدوده: این نسخه هسته عملیاتی فروش با کیف پول، رسید دستی، ساخت اکانت روی PasarGuard، ثبت سرویس و مدیریت ربات/CMD را فراهم می‌کند. ادعای برابری کامل با تمام امکانات میرزا ندارد. درگاه پرداخت خودکار، تمدید و افزایش حجم از طریق API پنل، تسویه کمیسیون نمایندگی، کد تخفیف/هدیه قابل مصرف، همگام‌سازی دوره‌ای مصرف و Mini App هنوز در این نسخه کامل نشده‌اند.

## امکانات پیاده‌سازی‌شده

### سمت کاربر
- منوی فارسی، پروفایل و مشاهده موجودی کیف پول.
- فهرست پلن‌های فعال با قیمت، حجم، مدت و Group IDs.
- خرید با کسر موجودی در تراکنش دیتابیس و ثبت دفتر کل مالی.
- ساخت اکانت روی PasarGuard و تحویل لینک اشتراک.
- نمایش سرویس‌ها، سرور، وضعیت، تاریخ انقضا و سقف حجم ثبت‌شده.
- درخواست شارژ با ارسال تصویر رسید و تأیید/رد دستی توسط مدیر.
- ثبت تیکت پشتیبانی.
- مسدودسازی کاربر و حالت تعمیرات برای مسیرهای خرید/شارژ.

### پنل مدیریت داخل تلگرام
- داشبورد کاربران، سفارش‌های موفق، فروش، رسیدهای معلق و تیکت‌های باز.
- روشن/خاموش‌کردن فروشگاه، شارژ کیف پول، پشتیبانی و حالت تعمیرات.
- ویرایش متن خوش‌آمدگویی، راهنمای پرداخت و پیام فروشگاه.
- افزودن پلن و سرور PasarGuard با تست ورود API قبل از ذخیره.
- فعال/غیرفعال‌کردن پلن‌ها و سرورها.
- افزایش موجودی، مسدود/آزادسازی کاربر و پیام همگانی با فاصله ارسال.
- مشاهده، پاسخ و بستن تیکت‌ها.
- افزودن و حذف مدیر دیتابیسی؛ مدیران اصلی با `ADMIN_IDS` کنترل می‌شوند.
- صف سفارش‌های با نتیجه نامشخص برای بررسی دستی پنل.
- ثبت رویدادهای مهم مدیریتی در Audit Log.

### دیتابیس
- PostgreSQL 15/16 برای Production با Connection Pool مبتنی بر psycopg 3.
- SQLite فقط برای توسعه و تست.
- Migration با Alembic.
- تراکنش مالی، قفل ردیف کاربر در PostgreSQL و جلوگیری از دوباره‌کاری با شناسه مرجع یکتا.
- جداول کاربران، سرورها، پلن‌ها، سفارش‌ها، سرویس‌ها، رسیدها، دفتر کل، تنظیمات، تیکت‌ها، مدیران، لاگ مدیریتی، پرداخت‌ها و رخدادهای سرویس.
- اسکریپت انتقال یک‌باره اطلاعات SQLite به PostgreSQL؛ فایل مبدأ حفظ می‌شود.

## نصب Ubuntu 24.04 یا جدیدتر

پس از قراردادن محتوای پروژه در ریشه مخزن عمومی GitHub با آدرس `amirtn9/sonarx` و شاخه `main`:

```bash
curl -fsSL https://raw.githubusercontent.com/amirtn9/sonarx/main/install.sh | sudo bash
```

یا سورس را دریافت کنید و نصب را اجرا کنید:

```bash
git clone https://github.com/amirtn9/sonarx.git
cd sonarx
sudo bash install.sh install
```

نصب‌کننده Python، PostgreSQL و وابستگی‌های سیستم را نصب می‌کند، دیتابیس و کاربر اختصاصی می‌سازد، محیط مجازی را آماده می‌کند، اطلاعات ربات را در اولین نصب می‌گیرد، کلید Fernet می‌سازد، Migration را اجرا و سرویس systemd را فعال می‌کند. اگر دیتابیس SQLite قبلی موجود باشد و نصب‌کننده PostgreSQL جدید بسازد، تلاش می‌کند داده‌های قبلی را منتقل کند و فایل SQLite را نگه می‌دارد.

**نکته:** این محیط به مخزن GitHub شما Push انجام نداده است. لینک نصب بالا زمانی فعال خواهد بود که فایل‌های همین پروژه را در ریشه مخزن قرار دهید و Push کنید.

## Docker Compose

```bash
cp .env.example .env
# BOT_TOKEN، ADMIN_IDS، FERNET_KEY و POSTGRES_PASSWORD را در .env تنظیم کنید
docker compose up -d --build
docker compose logs -f bot
```

Compose دیتابیس PostgreSQL و ربات را اجرا می‌کند. پیش از اجرای ربات، Migration انجام می‌شود. فایل `.env` را در Git قرار ندهید.

## پیکربندی اولیه

1. `BOT_TOKEN` و `ADMIN_IDS` را تنظیم کنید.
2. کلید Fernet بسازید:
   ```bash
   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```
3. برای Production مقدار `DATABASE_URL` را تنظیم کنید:
   ```text
   postgresql+psycopg://sonarx:YOUR_PASSWORD@127.0.0.1:5432/sonarx
   ```
4. ربات را اجرا کنید، `/start` را بزنید و وارد مدیریت شوید.
5. سرور پنل را اضافه کنید. نوع `pasarguard` (پیشنهادی)، `marzban` یا `marzneshin` را انتخاب کنید. قبل از ذخیره، ورود API بررسی می‌شود و رمز پنل با Fernet رمزنگاری می‌شود.
6. پلن بسازید و Group IDs را به شکل آرایه JSON عددی مانند `[1,2]` وارد کنید.
7. قبل از فروش واقعی، ساخت اکانت، لینک اشتراک، انقضا، حجم و Group ID را در یک پنل آزمایشی بررسی کنید.

## سازگاری پنل‌ها

Adapter اولویت‌دار PasarGuard از API سازگار با Marzban استفاده می‌کند:
- `POST /api/admin/token`
- `POST /api/user`
- `GET /api/user/{username}`

در درخواست ساخت اکانت PasarGuard فیلدهای `username`، `proxy_settings`، `data_limit`، `expire`، `data_limit_reset_strategy`، `group_ids` و note ارسال می‌شوند. Adapterهای Marzban و Marzneshin نیز endpointهای ورود و ساخت کاربر مخصوص خود را دارند. معنای Group/Service ID و مدل پاسخ در پنل‌ها متفاوت است؛ نسخه دقیق هر پنل را قبل از Production تست کنید. Adapter مربوط به Sanaei در این نسخه ادغام نشده است.

**ایمنی ساخت سرویس:** ابتدا ورود به سرورها بررسی می‌شود. پس از ارسال درخواست ساخت اکانت، اگر Timeout یا خطای مبهم رخ دهد، ربات روی پنل دیگری همان خرید را تکرار نمی‌کند و مبلغ را هم خودکار برنمی‌گرداند؛ سفارش در صف بررسی می‌ماند. مدیر باید ابتدا پنل را بررسی کند. دکمه بازپرداخت دستی فقط پس از اطمینان از ساخته‌نشدن اکانت (یا حذف آن از پنل) استفاده شود.

## دستورات CMD

پس از نصب، دستور `sonarx` در دسترس است:

```bash
sonarx status
sonarx start
sonarx stop
sonarx restart
sonarx logs
sonarx update

sonarx db-current
sonarx db-upgrade
sonarx backup --output /root/sonarx-backup.dump
sonarx restore /root/sonarx-backup.dump --yes

sonarx users
sonarx orders
sonarx admins
sonarx admin-add 123456789 --role operator
sonarx admin-remove 123456789
sonarx user-credit 123456789 500000 --ref manual-credit-001
sonarx user-block 123456789
sonarx user-block 123456789 --unblock
sonarx settings
sonarx setting-get feature.shop_enabled
sonarx setting-set feature.shop_enabled 0
```

`restore --yes` مخرب است؛ قبل از بازیابی Backup جدید بگیرید.

## توسعه و تست

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# برای تست محلی DATABASE_URL خالی باشد.
python -m unittest discover -s tests -v
python -m compileall -q sonarx
bash -n install.sh
```

برای مشاهده SQL مهاجرت PostgreSQL بدون اتصال واقعی:

```bash
DATABASE_URL='postgresql+psycopg://sonarx:pass@localhost:5432/sonarx' alembic upgrade head --sql
```

## انتشار در GitHub

1. مخزن `amirtn9/sonarx` را ایجاد یا تأیید کنید.
2. محتوای همین پروژه را در ریشه مخزن قرار دهید.
3. `.env`، رمز پنل، کلید Fernet، Backup و اطلاعات کاربران را Commit نکنید.
4. شاخه `main` را Push کنید. GitHub Actions تست‌ها، Compile، syntax نصب‌کننده و تولید SQL مهاجرت را اجرا می‌کند.
5. بعد از انتشار، دستور نصب یک‌خطی را اجرا کنید.

## چک‌لیست قبل از Production

- نسخه دقیق PasarGuard و API آن را در محیط آزمایشی بررسی کنید.
- لینک اشتراک، Group ID، تاریخ انقضا و حجم را آزمایش کنید.
- تأیید رسید و تطبیق دفتر کل را بررسی کنید.
- مطمئن شوید Timeout ساخت اکانت به صف بررسی می‌رود و باعث ساخت تکراری نمی‌شود.
- از PostgreSQL نسخه پشتیبان بگیرید و کلید Fernet را جداگانه نگه دارید؛ از دست رفتن کلید، رمزهای پنل را غیرقابل‌بازیابی می‌کند.
- TLS را روشن نگه دارید و دسترسی API پنل را محدود کنید.
- قبل از اتکا به پرداخت خودکار، درگاه پرداخت و Webhook واقعی را پیاده‌سازی و آزمایش کنید.
