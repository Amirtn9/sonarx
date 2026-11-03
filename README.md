# SonarX 5.2 — Telegram VPN Store

SonarX is an independent Telegram VPN sales and operations bot. Its product flow and administrative layout take inspiration from the supplied Mirza Bot project, while retaining SonarX's own codebase and branding. **PasarGuard is the primary supported panel adapter.** Secondary adapters are included for Marzban and Marzneshin; the supplied Sanaei/X-UI integration is not part of this release.

> Scope note: this is a working foundation for wallet-based sales, receipt review, PasarGuard account provisioning, service records and bot/CLI administration. It is not a claim of complete feature parity with Mirza. Automated payment-gateway settlement, service renewal/traffic top-ups on panel APIs, reseller commission settlement, coupon/gift-code redemption, scheduled usage sync and a WebApp are not represented as finished features in this build.

## Included

### Customer bot
- Persian customer-facing menu, account profile and wallet balance.
- Active plan catalogue with price, traffic, validity and PasarGuard Group IDs.
- Wallet purchase with a transactional balance deduction and unique ledger reference.
- PasarGuard user provisioning and delivery of the subscription URL.
- Service list with server, state, expiry and traffic limit fields.
- Manual deposit request using a receipt image; admin approval or rejection.
- Ticket creation and replies from the admin console.
- User blocking and maintenance-mode enforcement for purchase/deposit flows.

### Admin bot
- Dashboard: users, completed orders, recorded sales, pending receipts and open tickets.
- Runtime switches for store, deposits, support and maintenance mode.
- Editable welcome message, payment instructions and store notice.
- Add plans and PasarGuard servers; verify panel credentials before saving.
- Enable/disable plans and servers.
- Manual wallet credit, block/unblock users and broadcast with basic pacing.
- Ticket inbox, replies and closure.
- Add/remove database-based admin accounts; primary admins remain controlled by `ADMIN_IDS`.
- Manual review queue for uncertain panel provisioning outcomes.
- Audit log entries for critical administrative actions.

### Data layer
- PostgreSQL 15/16 recommended for production, via psycopg 3 connection pool.
- SQLite retained for local development and unit tests.
- Alembic migration baseline.
- Transactional wallet/ledger operations and row locking on PostgreSQL.
- Unique ledger references and guarded deposit/order state transitions.
- Tables: `users`, `servers`, `products`, `orders`, `services`, `deposits`, `ledger`, `settings`, `tickets`, `ticket_messages`, `admin_roles`, `audit_logs`, `payments`, `service_events`.
- A one-time SQLite-to-PostgreSQL migration script is included. The source SQLite database is preserved.

## Installation on Ubuntu 24.04+

After this repository is pushed to the public GitHub repository `amirtn9/sonarx` on branch `main`, install with:

```bash
curl -fsSL https://raw.githubusercontent.com/amirtn9/sonarx/main/install.sh | sudo bash
```

Or clone and run:

```bash
git clone https://github.com/amirtn9/sonarx.git
cd sonarx/sonarx_v5   # only if the repository is committed with this folder layout
sudo bash install.sh install
```

The installer installs Python, PostgreSQL and system packages; creates a PostgreSQL role/database; creates a Python virtual environment; prompts for Telegram credentials on first installation; generates a Fernet key; applies Alembic migrations; migrates an existing SQLite file if a new PostgreSQL database is created; and enables the systemd service.

The GitHub URL above is the intended deployment target, not a claim that this workspace has pushed files to GitHub. For the one-line installer to work, commit the contents of this project at the repository root (or adjust the raw URL to the actual subdirectory).

## Docker Compose

```bash
cp .env.example .env
# Set BOT_TOKEN, ADMIN_IDS, FERNET_KEY and POSTGRES_PASSWORD in .env
docker compose up -d --build
docker compose logs -f bot
```

The Compose file starts PostgreSQL and the bot. The bot runs `alembic upgrade head` before polling Telegram. `POSTGRES_PASSWORD` must be a long random value. Do not commit `.env`.

## First configuration

1. Set `BOT_TOKEN` and one or more numeric `ADMIN_IDS`.
2. Set `FERNET_KEY`. Generate one with:
   ```bash
   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```
3. Set `DATABASE_URL` for PostgreSQL in production, e.g.:
   ```text
   postgresql+psycopg://sonarx:YOUR_PASSWORD@127.0.0.1:5432/sonarx
   ```
4. Start the bot and open `/start`, then open the management panel.
5. Add a panel server. Choose `pasarguard` (recommended), `marzban` or `marzneshin`; the bot checks API login before storing credentials. Panel passwords are encrypted with Fernet.
6. Add a plan. Supply `Group IDs` as a JSON integer array, e.g. `[1,2]`.
7. Test account creation, subscription link response, expiration, data limit and the intended group assignment on a non-production/test user before accepting real payments.

## Panel compatibility

PasarGuard is the priority adapter and uses the Marzban-compatible API endpoints:
- `POST /api/admin/token`
- `POST /api/user`
- `GET /api/user/{username}`

The PasarGuard account payload includes `username`, `proxy_settings`, `data_limit`, `expire`, `data_limit_reset_strategy`, optional `group_ids` and a SonarX order note. Marzban and Marzneshin adapters are also present, using their respective token/user endpoints. Their account fields and group/service semantics differ; validate each exact panel version before production. The supplied Sanaei API wrapper has not yet been integrated into this build.

Important provisioning safety behavior: server login checks can fail over before the create request. After a create request has an ambiguous timeout/server failure, SonarX does **not** blindly create the same purchase on another panel or automatically refund it. The order is held in the review queue so an administrator can inspect PasarGuard first. A manual refund action exists in that queue; use it only after confirming no live user was created (or after removing that user from the panel).

## CMD operations

After installation, the `sonarx` command is available:

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

`restore --yes` is destructive. Take a fresh backup first. `service-remove` removes the systemd service and application files only when invoked through the installer; PostgreSQL data is preserved.

## Local development and tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Leave DATABASE_URL empty for SQLite tests.
python -m unittest discover -s tests -v
python -m compileall -q sonarx
bash -n install.sh
```

For PostgreSQL migration SQL preview:

```bash
DATABASE_URL='postgresql+psycopg://sonarx:pass@localhost:5432/sonarx' alembic upgrade head --sql
```

## GitHub publishing

1. Create/confirm the repository `amirtn9/sonarx`.
2. Put the contents of this project at repository root.
3. Review `.env.example`; never commit `.env`, panel credentials, Fernet keys, database dumps or customer data.
4. Push the `main` branch. GitHub Actions runs compile checks, SQLite tests, shell syntax checks and an offline Alembic PostgreSQL migration render.
5. Only then use the one-line `curl | sudo bash` installer.

## Production checklist

- Verify the exact PasarGuard version and API contract in a staging panel.
- Test subscription link delivery, group assignment, expiry and data limit.
- Confirm manual receipt review and wallet ledger balances.
- Verify a provisioning timeout enters manual review and does not trigger duplicate account creation.
- Back up PostgreSQL and securely retain the Fernet key. Losing the key makes encrypted panel passwords unrecoverable.
- Keep TLS verification enabled and restrict panel API credentials.
- Configure real payment gateway/webhook integration before treating receipts as automated payments.
- Review logs, backup restore and systemd restart behavior before production launch.
