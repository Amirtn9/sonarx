# SonarX 6.2.0 — Mirza Core Parity

- Added category-aware product browsing and product-to-category assignment.
- Added configurable free trial accounts with per-user quotas and safe panel-order handling.
- Added custom plan builder with per-GB/per-day pricing and owner-bound hidden products.
- Added Telegram contact verification and optional Iran-number validation.
- Added required-channel membership gate with re-check action.
- Added QR generation for subscription links and manual panel refresh for service usage/status/expiry.
- Added purchase/deposit cashback and referral/reseller commissions with idempotent ledger references.
- Added reseller application, admin approval/rejection and reseller dashboard.
- Reworked the dice reward into a cooldown-based dice/slot wheel with configurable rewards.
- Added a PostgreSQL migration and additive SQLite upgrades for the new data model.
- Feature matrix now visually distinguishes implemented options from modules that are not yet ported; unsupported toggles are blocked instead of being saved as if active.
- Added 9 new offline database tests; total test suite: 32 passing.

## Remaining Mirza integrations
Online payment providers, Mini App/web admin, additional panel drivers, manual config inventory, real bulk panel operations, protocol/location switching and scheduled background jobs still require separate integrations and are not represented as operational in this release.

# SonarX 6.1.0 — UI/UX redesign
- Replaced the long flat admin button wall with eight focused management sections and compact submenus.
- Rebuilt the customer dashboard as a two-column Inline menu with a balance/account summary.
- Added dedicated plan detail, service list and service detail screens; main navigation edits the active screen instead of flooding the chat.
- Introduced mutually exclusive keyboard modes: Inline keyboards for navigation and Reply keyboards only for text entry, final purchase/add-on confirmation, back/cancel and ticket closure.
- Clear the active Inline screen before entering Reply mode; remove the Reply keyboard before restoring an Inline screen. A per-chat mode guard clears legacy Reply keyboards on first screen render.
- Added final purchase and service add-on quote/confirmation steps using Telegram's Reply keyboard.
- Added two-step native-keyboard approval/rejection for deposits and explicit confirmation before uncertain-order refunds.
- Destructive custom-link deletion now requires a separate Reply Keyboard confirmation.
- Added customer ticket conversation detail and reply screens; admins can reply or close tickets from a dedicated Reply keyboard.
- Split general settings into operational toggles and bot-text editing screens; grouped feature toggles by category and two-column layout.
- Reworked button management into a list and per-button detail screen.
- Improved navigation breadcrumbs, empty states, status labels and return paths across admin screens.

# SonarX 6.0.0
- Reworked interactive installer prompts to read from /dev/tty, fixing curl | sudo bash menu input.
- Added CLI commands for secure bot-token rotation, admin ID updates, redacted config inspection, diagnostics, and detailed logs.
- Added expanded admin center, feature matrix, categories, discounts, gift-credit codes, resellers, channel settings, button toggles, reports, and audit log.
- Added PostgreSQL/SQLite schema and Alembic migration for extended settings tables.
- Added discount-code redemption during checkout and wallet-credit gift-code redemption.
- Explicitly documented unfinished integrations (online gateway, mini app, scheduled sync, bulk panel account creation).


## 6.3.0 — WizWiz-inspired architecture and operations

- Added a first-page project map and organized architecture/documentation directories.
- Added periodic order recovery: stale processing orders move to manual review; no unsafe auto-refund.
- Added panel-backed service snapshot synchronization for usage, quota, expiry, status and subscription URL.
- Added review-order reconciliation: fetch an already-created account from its original panel and complete the local order without issuing another create request.
- Fixed PostgreSQL natural-primary-key inserts for discount codes, gift codes and resellers.
- Enforced discount expiry and fail-closed handling of malformed expiry timestamps.
- Masked database connection strings in CLI configuration output.
- Rechecked shop availability at final purchase execution.
- Prevented sensitive user workflows and the admin dashboard from being exposed in group chats.
- Added regression tests for PostgreSQL adapter behavior, recovery, sync and review reconciliation.
- Added a systemd maintenance timer and CLI commands `sonarx recover-orders` / `sonarx sync-services`.

Reference scope: WizWiz product features were used as a functional comparison checklist. Its PHP implementation was not copied into SonarX.
