# ارتقا از SonarX 5.2 به 6.0

1. پیش از ارتقا بکاپ دیتابیس بگیرید:
   ```bash
   sonarx backup --output /root/sonarx-before-6.dump
   ```
2. سورس نسخه 6.0 را در شاخه `main` مخزن GitHub منتشر کنید؛ فایل `.env` و داده‌های سرور را Commit نکنید.
3. روی سرور:
   ```bash
   cd /opt/sonarx
   sudo bash install.sh update
   ```
   یا:
   ```bash
   git pull --ff-only
   .venv/bin/pip install -r requirements.txt
   .venv/bin/alembic upgrade head
   sudo systemctl restart sonarx
   ```
4. بررسی:
   ```bash
   sonarx doctor
   sonarx db-current
   sonarx logs -n 100
   ```
5. تنظیمات جدید را از پنل مدیریت بررسی کنید. قیمت هر GB و هر روز را از بخش مالی تنظیم کنید و سپس قابلیت‌های «افزایش حجم» و «تمدید سرویس» را فعال کنید.

Migration نسخه 6.0 افزایشی است و جداول قبلی را حذف نمی‌کند. فایل `.env`، کلید Fernet و PostgreSQL data volume باید حفظ شوند. قبل از هر ارتقا بکاپ داشته باشید.
