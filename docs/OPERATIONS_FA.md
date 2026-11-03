# عملیات و نگهداری SonarX

## نصب و بروزرسانی

```bash
sudo bash install.sh
```

برای نصب غیرتعاملی/اجرای مستقیم:
```bash
sudo bash install.sh install
sudo bash install.sh update
```

## CLI

```bash
sonarx status
sonarx doctor
sonarx logs -n 200 -f
sonarx token-change
sonarx admins-set 123456789,987654321
sonarx config show
sonarx config get DATABASE_URL
sonarx recover-orders
sonarx sync-services
```

مقادیر محرمانه از خروجی `config show/get` ماسک می‌شوند.

## نقش‌های مدیران

مدیر اصلی که در `ADMIN_IDS` تعریف شده، نقش `owner` دارد. برای مدیران جانبی از حداقل سطح لازم استفاده کنید:

```bash
sonarx admin-add 123456789 --role operator
sonarx admin-add 234567890 --role finance
sonarx admin-add 345678901 --role support
sonarx admin-add 456789012 --role marketing
sonarx admin-add 567890123 --role viewer
```

نقش‌های معتبر: `owner`، `admin`، `operator`، `finance`، `support`، `marketing` و `viewer`. اجرای دوباره `admin-add` برای همان شناسه، نقش فعال آن مدیر را به‌روزرسانی می‌کند. حذف دسترسی با `sonarx admin-remove ID` انجام می‌شود.

## زمان‌بندی

`sonarx-maintenance.timer` به‌صورت پیش‌فرض هر ۱۰ دقیقه:
1. سفارش‌های قدیمی در وضعیت `processing` را به `review` می‌برد؛ بازپرداخت خودکار نمی‌کند.
2. مصرف و وضعیت سرویس‌ها را از پنل می‌خواند و Snapshot دیتابیس را بروزرسانی می‌کند.

مشاهده وضعیت:
```bash
systemctl status sonarx-maintenance.timer
systemctl list-timers sonarx-maintenance.timer
journalctl -u sonarx-maintenance.service -n 100 --no-pager
```

تنظیم:
- `ORDER_RECOVERY_MINUTES=15`
- `SERVICE_SYNC_BATCH=500`

## نکته عملیاتی

همگام‌سازی سرویس فقط خواندنی در پنل است. حذف یا تعلیق خودکار اکانت در این Worker فعال نیست تا خطای موقت API باعث قطع سرویس کاربر نشود.
