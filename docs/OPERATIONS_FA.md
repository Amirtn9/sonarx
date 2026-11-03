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
