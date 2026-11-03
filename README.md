# SonarX — Project Index

**Start here:** [Project Map](PROJECT_MAP.md) · [Sitemap](SITEMAP.md) · [Docs Index](docs/README_FA.md) · [WizWiz Feature Parity](docs/WIZWIZ_PARITY_FA.md)

```text
SonarX/
├── sonarx/       # Telegram bot, database, panels, CLI and workers
│   ├── handlers/ # Telegram handler modules
│   ├── services/ # Business workflows
│   ├── repositories/ # Data access modules
│   ├── panels/   # VPN panel adapters
│   └── workers/  # Background jobs
├── migrations/   # PostgreSQL/Alembic migrations
├── tests/        # Unit and regression tests
├── scripts/      # Migration and maintenance tools
├── systemd/      # Bot service and maintenance timer
├── docs/         # Architecture, operations, security and parity docs
├── install.sh    # Install / update / remove
└── PROJECT_MAP.md
```

# SonarX 6.2.0 — Telegram VPN Store

SonarX is an independent Telegram VPN sales and administration bot. The admin information architecture is based on the supplied feature screenshots and Mirza Bot reference, while keeping SonarX branding and implementation independent. **PasarGuard is the primary panel adapter**; Marzban and Marzneshin are secondary adapters.

## Customer features
- Two-column Inline Keyboard dashboard for navigation and dedicated detail screens.
- Mutually exclusive keyboard modes: Inline for navigation; Telegram Reply Keyboard only for text entry, final confirmation, deposit approve/reject, refund confirmation, custom-link deletion, back/cancel and ticket closure. The active Inline menu is cleared before Reply mode, and Reply mode is removed before the next Inline screen.
- The `/start` command clears legacy persistent Reply keyboards from earlier versions.
- Plan catalogue, wallet purchase, PasarGuard provisioning and subscription delivery.
- Manual receipt-based wallet deposits with one-time admin approval.
- Service/order history, support tickets, FAQ and tutorials.
- Gift wallet-credit codes, percentage discounts during checkout, referral links and cooldown-based dice/slot prize wheel.
- Category-aware product catalogue and product/category assignment.
- Quota-limited free trial accounts and custom plans priced per GB/per day.
- Subscription QR codes and manual service refresh from the connected panel.
- Telegram contact verification, optional Iran-number validation and required-channel membership gate.
- Purchase/deposit cashback, reseller commissions, reseller applications and reseller dashboard.

Telegram accepts only one `reply_markup` per message. SonarX also switches modes across screens: before a Reply Keyboard prompt, the active Inline menu is removed; after the prompt, the Reply Keyboard is removed before an Inline screen is rendered.

## UI/UX structure
- Customer dashboard with account balance, service list/detail, plan detail and help center.
- Admin console split into eight focused sections instead of one long button list.
- Main navigation edits the current screen when possible; each submenu has a clear return path.
- Purchase and service add-on operations show a price preview and use a Reply Keyboard for explicit final confirmation. Deposit approval/rejection and uncertain-order refunds also require a separate native-keyboard confirmation step.

## Admin console
- Sales/user/deposit/ticket dashboard and reports.
- Plan, category, server and panel connection management, including PasarGuard connection tests.
- User search, block/unblock, wallet credit and broadcast.
- Admin roles, audit events and uncertain-order review queue.
- Discount and gift-credit codes, reseller records, channel destinations and text posts.
- Home button label/visibility controls and custom HTTPS link buttons.
- Configurable feature matrix grouped into sales, finance, customer support, technical display and security.

## Server-side operations
`install.sh` supports an interactive operations menu, direct install/update/status/logs, and reads interactive prompts from `/dev/tty` so `curl | sudo bash` does not consume the menu input. The CLI supports status/doctor, detailed logs, token rotation with Telegram `getMe` validation, primary admin-ID updates, redacted configuration inspection, safe config changes, backups, restores and service control.

```bash
curl -fsSL https://raw.githubusercontent.com/Amirtn9/sonarx/main/install.sh | sudo bash
# Or direct:
bash <(curl -fsSL https://raw.githubusercontent.com/Amirtn9/sonarx/main/install.sh) install
```

## Data
- PostgreSQL for production using psycopg 3 connection pooling; SQLite for local tests.
- Alembic migrations, including additive SonarX 6.0 tables.
- Transactional wallet ledger, unique references, guarded deposit decisions and order-state transitions.
- SQLite-to-PostgreSQL migration script preserving the source file.

## Scope honesty
The feature matrix includes the full set of settings represented in the supplied screenshots. Some switches are configuration placeholders until their service module is implemented. In particular, bulk/automatic renewals, real bulk provisioning, scheduled usage synchronization, automated online payment settlement, Mini App/WebApp, automated lottery scheduling, phone verification, and reseller commission settlement are **not production-complete in this release**. The current gift-code flow credits the wallet; it does not yet add panel traffic or validity time.

## Commands
```bash
sonarx status
sonarx doctor
sonarx logs -n 200 -f
sonarx token-change
sonarx admins-set 123456789,987654321
sonarx config show
sonarx config set CURRENCY_LABEL toman
sonarx backup --output /root/sonarx-backup.dump
sonarx restore /root/sonarx-backup.dump --yes
```

Do not commit `.env`, panel passwords, Fernet keys, backups or user data. Rotate any bot token that has been exposed in a shared file. The GitHub repository must be updated with this release before the one-line install URL points to it; this environment does not push commits to GitHub.


## 6.2.0 — Mirza core parity

This release ports core Mirza-style flows into SonarX's Python architecture: categorized products, quota-limited free trials, custom plans, QR subscriptions, manual service refresh, Telegram contact verification, required-channel membership, cashback/referral/reseller commissions, reseller requests and dashboard, and cooldown-based prize wheel. See `FEATURE_MATRIX_FA.md` for the explicit implementation boundary and remaining integrations.
