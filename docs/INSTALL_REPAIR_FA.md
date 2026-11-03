# راهنمای تعمیر نصب SonarX 6.3.2

## مشکل گزارش‌شده در ۳ نوامبر ۲۰۲۶

پیام `.env.example is missing from the GitHub release` پس از `git clone` و نصب بسته‌ها رخ می‌داد.
مشکل به فقدان فایل مخفی `.env.example` در مخزن مربوط بود، نه خرابی Python/PostgreSQL.

## اصلاحات

- Installer ابتدا `.env.example`، سپس فایل قابل‌مشاهده `env.example` را می‌خواند و در صورت فقدان هر دو از الگوی داخلی ایمن در `/var/lib/sonarx/env.defaults` استفاده می‌کند.
- از ساخت فایل untracked در مسیر Git خودداری می‌کند.
- فایل `.env` بعد از تنظیم در `/var/lib/sonarx/.env` ذخیره می‌شود و حذف/نصب مجدد آن را باز می‌گرداند.
- حذف برنامه نسخه جدید، از تنظیمات و دیتای SQLite نسخه قبلی بکاپ جداگانه می‌گیرد؛ PostgreSQL دست‌نخورده باقی می‌ماند.
- در صورت وجود دیتابیس PostgreSQL قدیمی بدون تنظیمات، نصب‌کننده هشدار می‌دهد و بدون تأیید صریح گذرواژه و کلید Fernet جدید نمی‌سازد.
- `CI` وجود فایل `.env.example` را روی GitHub بررسی می‌کند.

## هشدار درباره نصب‌های قدیمی

نصب‌کننده نسخه قدیمی ممکن است `/opt/sonarx/.env` را حذف کرده باشد. پایگاه داده PostgreSQL به‌تنهایی برای رمزگشایی گذرواژه‌های پنل کافی نیست: کلید Fernet باید از بکاپ قبلی بازیابی شود. هیچ نصب‌کننده‌ای نمی‌تواند یک کلید از دست رفته را از داده رمزنگاری شده استخراج کند.

## انتشار و نصب

ابتدا محتویات ZIP را به ریشه شاخه `main` مخزن GitHub آپلود کنید. فایل قابل مشاهده `env.example` را حتماً در GitHub منتشر کنید؛ در حالت ایده‌آل `.env.example` هم وجود داشته باشد. بعد:

```bash
curl -fsSL https://raw.githubusercontent.com/Amirtn9/sonarx/main/install.sh -o /root/sonarx-install.sh
sudo bash /root/sonarx-install.sh install
```

برای بررسی قبل از نصب:

```bash
ls -la /opt/sonarx/.env /var/lib/sonarx/.env /opt/sonarx/.env.example 2>/dev/null || true
```

پس از نصب:

```bash
sonarx doctor
sonarx db-current
sonarx logs -n 100
systemctl status sonarx --no-pager
```

قبل از حذف یا بروزرسانی پروژه از PostgreSQL نسخه پشتیبان مستقل تهیه کنید.
