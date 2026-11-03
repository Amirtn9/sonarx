# SonarX 6.0 — Telegram VPN Store

SonarX is an independent Telegram VPN sales and administration bot. The admin information architecture is based on the supplied feature screenshots and Mirza Bot reference, while keeping SonarX branding and implementation independent. **PasarGuard is the primary panel adapter**; Marzban and Marzneshin are secondary adapters.

## Customer features
- Persistent Telegram Reply Keyboard for navigation plus Inline Keyboards for in-message actions.
- Plan catalogue, wallet purchase, PasarGuard provisioning and subscription delivery.
- Manual receipt-based wallet deposits with one-time admin approval.
- Service/order history, support tickets, FAQ and tutorials.
- Gift wallet-credit codes, percentage discounts during checkout, referral links and dice rewards (feature switches control availability).

Telegram accepts only one `reply_markup` per message. SonarX therefore uses the persistent Reply Keyboard on the welcome/navigation message and Inline Keyboards in subsequent feature screens rather than attempting to attach both markup types to the same message.

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
