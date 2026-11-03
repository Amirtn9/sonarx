# Changelog

## 5.2.0
- Added PostgreSQL production backend with psycopg connection pooling and Alembic migration.
- Retained SQLite for local development and automated tests.
- Added service records, ticket conversations, admin roles, audit log, payments and service event tables.
- Added safe provisioning behavior: uncertain panel creation outcomes are held for manual review instead of automatic cross-panel retries/refunds.
- Expanded Telegram admin panel with feature switches, editable text, plan/server activation, broadcast, ticket management, admin management and review queue.
- Added CLI operations for systemd, database migrations, PostgreSQL backup/restore, user management, admin roles and settings.
- Added Ubuntu installer, systemd unit, Docker Compose and GitHub Actions checks.
- Added SQLite-to-PostgreSQL migration helper.
