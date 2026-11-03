# نقشه پروژه SonarX

> این فایل نقشه اصلی مخزن است. نقطه شروع مطالعه و توسعه پروژه همین صفحه است.

## نمای پوشه‌ها

```text
SonarX/
├── README.md                    # صفحه اول مخزن و راهنمای سریع
├── README_FA.md                 # راهنمای فارسی
├── PROJECT_MAP.md               # نقشه معماری و مسیر فایل‌ها
├── SITEMAP.md                   # نقشه ناوبری مستندات و ماژول‌ها
├── docs/
│   ├── README_FA.md             # فهرست مستندات
│   ├── WIZWIZ_PARITY_FA.md      # تطبیق قابلیت‌ها با مرجع WizWiz
│   ├── OPERATIONS_FA.md         # عملیات نصب، نگهداری و عیب‌یابی
│   └── SECURITY_FA.md           # اصول امنیتی و چک‌لیست انتشار
├── sonarx/
│   ├── bot.py                   # ورودی سازگار تلگرام (فعلاً نگهداری شده)
│   ├── cli.py                   # مدیریت CMD/CLI
│   ├── config.py                # تنظیمات محیطی
│   ├── db.py                    # لایه تراکنش و Repositoryهای فعلی
│   ├── pasarguard.py            # Adapterهای PasarGuard/Marzban/Marzneshin
│   ├── schema_postgres.sql      # مرجع ساختار PostgreSQL
│   ├── security.py              # رمزنگاری اطلاعات محرمانه
│   ├── handlers/                # مقصد تفکیک Handlerهای تلگرام
│   ├── services/                # مقصد منطق کاربردی فروش، مالی و سرویس
│   ├── repositories/            # مقصد Repositoryهای مستقل
│   ├── panels/                  # قرارداد و Adapterهای پنل‌ها
│   ├── workers/
│   │   ├── order_recovery.py    # بازیابی سفارش‌های نیمه‌تمام
│   │   └── service_sync.py      # همگام‌سازی مصرف و وضعیت سرویس
│   └── auth/                    # مقصد کنترل دسترسی و سیاست‌های امنیتی
├── migrations/
│   └── versions/                # مهاجرت‌های Alembic
├── tests/                       # تست‌های واحد و Regression
├── scripts/                     # ابزارهای مهاجرت و نگهداری
├── systemd/
│   ├── sonarx.service           # سرویس اصلی ربات
│   ├── sonarx-maintenance.service
│   └── sonarx-maintenance.timer # اجرای دوره‌ای Recovery و Sync
├── install.sh                   # نصب، بروزرسانی و حذف
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## مسیر توسعه هر قابلیت

| حوزه | محل فعلی/اصلی | مسئولیت |
|---|---|---|
| رابط تلگرام | `sonarx/bot.py` | منوها، callbackها و جریان مکالمه |
| مدیریت CLI | `sonarx/cli.py` | نصب، وضعیت، لاگ، بکاپ و عملیات نگهداری |
| دیتابیس | `sonarx/db.py` | تراکنش‌ها، کیف پول، سفارش و کاربران |
| PostgreSQL | `sonarx/schema_postgres.sql` و `migrations/` | ساختار و ارتقای دیتابیس |
| پنل‌ها | `sonarx/pasarguard.py` | ارتباط با API پنل‌ها |
| رمزنگاری | `sonarx/security.py` | رمزنگاری رمز پنل و اسرار |
| بازیابی سفارش | `sonarx/workers/order_recovery.py` | انتقال سفارش‌های گیرکرده به صف بررسی |
| همگام‌سازی سرویس | `sonarx/workers/service_sync.py` | خواندن مصرف، حجم، انقضا و وضعیت از پنل |
| تست | `tests/` | Regression و جلوگیری از برگشت باگ |

## قرارداد معماری

- `sonarx.bot` همچنان نقطه ورود سازگار با نسخه‌های قبلی است؛ جابه‌جایی یک‌باره آن انجام نشده تا نصب و اجرای موجود نشکند.
- قابلیت‌های جدید باید در `services/`، `repositories/`، `panels/` یا `workers/` پیاده شوند؛ نه با افزودن منطق بیشتر به Handlerهای تلگرام.
- همه تغییرات دیتابیس باید با Migration نسخه‌بندی شوند.
- هر عملیات مالی باید مرجع یکتا (idempotency reference) و ثبت دفتر کل داشته باشد.
- عملیات پنل در حالت نتیجه نامشخص نباید به‌صورت خودکار تکرار یا بازپرداخت شود.
- هیچ قابلیت ناتمامی نباید با سوئیچ فعال‌سازی ظاهری به‌عنوان قابلیت عملیاتی معرفی شود.
