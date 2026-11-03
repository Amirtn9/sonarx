#!/usr/bin/env bash
set -Eeuo pipefail
REPO_URL="${SONARX_REPO_URL:-https://github.com/Amirtn9/sonarx.git}"
BRANCH="${SONARX_BRANCH:-main}"
APP_DIR="${SONARX_APP_DIR:-/opt/sonarx}"
SERVICE="sonarx"
PERSIST_DIR="${SONARX_PERSIST_DIR:-/var/lib/sonarx}"
BACKUP_DIR="${SONARX_BACKUP_DIR:-/var/backups/sonarx}"
LOG_FILE="${SONARX_LOG_FILE:-/var/log/sonarx-install.log}"
if [[ "${SONARX_INSTALL_LIB_ONLY:-0}" != "1" ]]; then
  mkdir -p "$(dirname "$LOG_FILE")"
  exec > >(tee -a "$LOG_FILE") 2>&1
fi

c(){ if [[ -t 1 ]]; then printf '\033[%sm%s\033[0m\n' "$1" "$2"; else printf '%s\n' "$2"; fi; }
ok(){ c 32 "[OK] $*"; }
info(){ c 36 "[INFO] $*"; }
warn(){ c 33 "[WARN] $*"; }
fail(){ c 31 "[ERROR] $*"; }
need_root(){ [[ "${EUID}" -eq 0 ]] || { fail "Run as root: sudo bash install.sh"; exit 1; }; }
prompt(){ local __var="$1" __label="$2" __secret="${3:-0}" __value=""; [[ -r /dev/tty ]] || { fail "Interactive terminal required for this step."; exit 1; }; if [[ "$__secret" == 1 ]]; then read -r -s -p "$__label" __value </dev/tty; echo; else read -r -p "$__label" __value </dev/tty; fi; printf -v "$__var" '%s' "$__value"; }
# Secrets are sent to Python over stdin, not process command-line arguments.
set_env(){
  local key="$1" value="$2"
  printf '%s' "$value" | "$APP_DIR/.venv/bin/python" -c '
from pathlib import Path
import sys
p=Path(sys.argv[1]); key=sys.argv[2]; value=sys.stdin.read()
lines=p.read_text().splitlines() if p.exists() else []
lines=[line for line in lines if not line.startswith(key+"=")]
lines.append(key+"="+value)
p.write_text("\n".join(lines)+"\n")
' "$APP_DIR/.env" "$key"
}
get_env(){
  "$APP_DIR/.venv/bin/python" - "$APP_DIR/.env" "$1" <<'PY'
import sys
from dotenv import dotenv_values
print(dotenv_values(sys.argv[1]).get(sys.argv[2]) or "")
PY
}
install_deps(){
  info "Installing system dependencies and PostgreSQL 16..."
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates openssl python3 python3-venv python3-dev build-essential libpq-dev postgresql postgresql-contrib postgresql-client
  python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
  systemctl enable --now postgresql
}
fetch_source(){
  if [[ -d "$APP_DIR/.git" ]]; then
    git -C "$APP_DIR" fetch origin "$BRANCH"
    git -C "$APP_DIR" checkout "$BRANCH"
    git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
  elif [[ -e "$APP_DIR" ]]; then
    fail "$APP_DIR exists but is not a Git checkout. Preserve it; inspect before replacing."
    exit 1
  else
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  fi
}
setup_pg(){
  local pass
  pass="$(openssl rand -hex 32)"
  if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='sonarx'" | grep -q 1; then
    printf "ALTER ROLE sonarx WITH LOGIN PASSWORD '%s';\n" "$pass" | runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 >/dev/null
  else
    printf "CREATE ROLE sonarx LOGIN PASSWORD '%s';\n" "$pass" | runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 >/dev/null
  fi
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='sonarx'" | grep -q 1; then
    runuser -u postgres -- createdb -O sonarx sonarx >/dev/null
  else
    runuser -u postgres -- psql -q -c "ALTER DATABASE sonarx OWNER TO sonarx" >/dev/null
  fi
  printf '%s' "$pass"
}
# The GitHub web uploader sometimes omits dotfiles, including .env.example.
# Never make a missing public template fatal: create a safe template OUTSIDE the
# Git working tree so future `git pull` operations cannot conflict with it.
ensure_env_template(){
  if [[ -f "$APP_DIR/.env.example" ]]; then
    printf '%s\n' "$APP_DIR/.env.example"
    return 0
  fi
  if [[ -f "$APP_DIR/env.example" ]]; then
    printf '%s\n' "$APP_DIR/env.example"
    return 0
  fi
  warn "Missing template in GitHub checkout; using built-in safe defaults." >&2
  mkdir -p "$PERSIST_DIR"
  chmod 700 "$PERSIST_DIR"
  cat > "$PERSIST_DIR/env.defaults" <<'DEFAULT_ENV'
BOT_TOKEN=replace_me
ADMIN_IDS=123456789
DATABASE_URL=
DATABASE_PATH=data/sonarx.sqlite3
DB_POOL_SIZE=10
TIMEZONE=Asia/Tehran
PASARGUARD_VERIFY_TLS=true
FERNET_KEY=generate_with_python_cryptography
CURRENCY_LABEL=تومان
SUPPORT_USERNAME=
POSTGRES_PASSWORD=
ORDER_RECOVERY_MINUTES=15
SERVICE_SYNC_BATCH=500
DEFAULT_ENV
  chmod 600 "$PERSIST_DIR/env.defaults"
  printf '%s\n' "$PERSIST_DIR/env.defaults"
}

# Save the database URL, Fernet key and bot identity, NOT just the database.
# Database-only backups cannot recover Fernet-encrypted panel credentials.
backup_config(){
  [[ -f "$APP_DIR/.env" ]] || return 0
  mkdir -p "$PERSIST_DIR" "$BACKUP_DIR/config"
  chmod 700 "$PERSIST_DIR" "$BACKUP_DIR" "$BACKUP_DIR/config"
  install -m 600 "$APP_DIR/.env" "$PERSIST_DIR/.env"
  cp -p "$APP_DIR/.env" "$BACKUP_DIR/config/sonarx-$(date +%Y%m%d-%H%M%S)-$$.env"
  chmod 600 "$PERSIST_DIR/.env" "$BACKUP_DIR/config/"*.env
}

# When an old PostgreSQL database exists but .env was already deleted by an
# older uninstaller, DO NOT silently reset DB credentials and the Fernet key.
confirm_lost_configuration(){
  local existing
  existing="$(runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='sonarx'" 2>/dev/null || true)"
  [[ "$existing" == "1" ]] || return 0
  warn "An existing SonarX PostgreSQL database was found, but the original .env is missing."
  warn "Its encrypted panel passwords require the ORIGINAL FERNET_KEY to decrypt."
  warn "A fresh Fernet key CANNOT recover previously encrypted panel credentials."
  warn "Check backups in $PERSIST_DIR or $BACKUP_DIR/config before continuing."
  local answer
  prompt answer "To keep the existing database but create NEW credentials, type RECONFIGURE: "
  [[ "$answer" == "RECONFIGURE" ]] || { fail "Install stopped safely. Restore original .env and retry."; return 1; }
}

configure(){
  local migrated=0 dburl key token admins pass template
  if [[ ! -f "$APP_DIR/.env" ]]; then
    if [[ -f "$PERSIST_DIR/.env" ]]; then
      info "Restoring preserved configuration from $PERSIST_DIR/.env"
      install -m 600 "$PERSIST_DIR/.env" "$APP_DIR/.env"
    else
      template="$(ensure_env_template)"
      cp "$template" "$APP_DIR/.env"
      chmod 600 "$APP_DIR/.env"
    fi
  fi
  if [[ -z "$(get_env BOT_TOKEN)" || "$(get_env BOT_TOKEN)" == "replace_me" ]]; then
    prompt token "Telegram BOT_TOKEN (input hidden): " 1
    prompt admins "Telegram ADMIN_IDS (comma-separated numeric IDs): "
    [[ -n "$token" && -n "$admins" ]] || { fail "BOT_TOKEN and ADMIN_IDS are required."; exit 1; }
    printf '%s\n%s\n' "$token" "$admins" | "$APP_DIR/.venv/bin/python" -c '
import re,sys,urllib.request,json
lines=sys.stdin.read().splitlines()
if len(lines)!=2: raise SystemExit("Missing bot token or admin IDs")
token,ids=lines
if not re.fullmatch(r"[0-9]{5,15}:[A-Za-z0-9_-]{20,}",token): raise SystemExit("Invalid Telegram token format")
admins=[x.strip() for x in ids.split(",") if x.strip()]
if not admins or not all(re.fullmatch(r"[0-9]{4,20}",x) for x in admins): raise SystemExit("ADMIN_IDS must be numeric")
try:
    req=urllib.request.Request("https://api.telegram.org/bot"+token+"/getMe")
    with urllib.request.urlopen(req,timeout=15) as r: data=json.loads(r.read())
except Exception: raise SystemExit("Unable to validate bot token against Telegram; check token and network.")
if not data.get("ok"): raise SystemExit("Telegram rejected the token")
print("Validated bot:", "@"+data["result"].get("username",""))
'
    set_env BOT_TOKEN "$token"
    set_env ADMIN_IDS "$admins"
  else
    info "Existing/previously restored BOT_TOKEN preserved."
  fi
  token="$(get_env BOT_TOKEN)"
  admins="$(get_env ADMIN_IDS)"
  if [[ -z "$token" || "$token" == "replace_me" ]]; then
    prompt token "Telegram BOT_TOKEN (input hidden): " 1
    set_env BOT_TOKEN "$token"
  fi
  if [[ -z "$admins" || "$admins" == "123456789" ]]; then
    prompt admins "Telegram ADMIN_IDS (comma-separated numeric IDs): "
    set_env ADMIN_IDS "$admins"
  fi
  "$APP_DIR/.venv/bin/python" - "$APP_DIR/.env" <<'PY'
import sys,re
from dotenv import dotenv_values
v=dotenv_values(sys.argv[1]); token=v.get("BOT_TOKEN",""); ids=v.get("ADMIN_IDS","")
if not re.fullmatch(r"[0-9]{5,15}:[A-Za-z0-9_-]{20,}",token): raise SystemExit("BOT_TOKEN format is invalid.")
items=[x.strip() for x in ids.split(",") if x.strip()]
if not items or any(not re.fullmatch(r"[0-9]{4,20}",x) for x in items): raise SystemExit("ADMIN_IDS must contain numeric Telegram IDs.")
PY
  dburl="$(get_env DATABASE_URL)"
  if [[ -z "$dburl" ]]; then
    # Re-check even if an earlier failed attempt left a placeholder .env.
    if [[ ! -f "$PERSIST_DIR/.env" ]]; then
      confirm_lost_configuration || return 1
    fi
    pass="$(setup_pg)"
    dburl="postgresql+psycopg://sonarx:${pass}@127.0.0.1:5432/sonarx"
    set_env DATABASE_URL "$dburl";set_env POSTGRES_PASSWORD "$pass";migrated=1
    ok "Local PostgreSQL role/database created."
  else
    info "Existing DATABASE_URL preserved."
  fi
  key="$(get_env FERNET_KEY)"
  if [[ -z "$key" || "$key" == "generate_with_python_cryptography" ]]; then
    key="$("$APP_DIR/.venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    set_env FERNET_KEY "$key"
  fi
  chmod 600 "$APP_DIR/.env"
  backup_config
  printf '%s\n' "$migrated" > "$APP_DIR/.migration-needed"
}
install_app(){
  need_root
  install_deps
  fetch_source
  cd "$APP_DIR"
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip wheel
  .venv/bin/pip install -r requirements.txt
  configure
  id sonarx >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin sonarx
  mkdir -p data logs
  chown -R root:sonarx "$APP_DIR"
  chown -R sonarx:sonarx data logs
  chmod 640 .env
  install -m 0644 systemd/sonarx.service /etc/systemd/system/sonarx.service
  install -m 0644 systemd/sonarx-maintenance.service /etc/systemd/system/sonarx-maintenance.service
  install -m 0644 systemd/sonarx-maintenance.timer /etc/systemd/system/sonarx-maintenance.timer
  cat >/usr/local/bin/sonarx <<'WRAPPER'
#!/usr/bin/env bash
cd /opt/sonarx
exec /opt/sonarx/.venv/bin/python -m sonarx.cli "$@"
WRAPPER
  chmod 0755 /usr/local/bin/sonarx
  .venv/bin/alembic upgrade head
  if [[ "$(cat .migration-needed)" == 1 ]]; then
    sqlite_path="$(get_env DATABASE_PATH)"
    if [[ -n "$sqlite_path" && -f "$sqlite_path" ]]; then
      .venv/bin/python scripts/migrate_sqlite_to_postgres.py --sqlite "$sqlite_path"
    fi
  fi
  rm -f .migration-needed
  systemctl daemon-reload
  systemctl enable --now "$SERVICE"
  systemctl enable --now sonarx-maintenance.timer
  ok "SonarX installed. Run: sonarx status"
  info "Admin commands: sonarx token-change | sonarx admins-set ID1,ID2 | sonarx logs -n 200 -f"
}
update_app(){
  need_root
  [[ -d "$APP_DIR/.git" ]] || { fail "SonarX is not installed."; exit 1; }
  local old_head
  old_head="$(git -C "$APP_DIR" rev-parse HEAD)"
  backup_config
  systemctl stop "$SERVICE" || true
  mkdir -p /var/backups/sonarx
  chmod 700 /var/backups/sonarx
  if [[ "$(get_env DATABASE_URL)" == postgresql* ]]; then
    if ! /usr/local/bin/sonarx backup --output "/var/backups/sonarx/pre-update-$(date +%Y%m%d-%H%M%S).dump"; then
      systemctl restart "$SERVICE" || true
      fail "Pre-update database backup failed; update cancelled."
      return 1
    fi
  elif [[ -n "$(get_env DATABASE_PATH)" && -f "$APP_DIR/$(get_env DATABASE_PATH)" ]]; then
    if ! cp "$APP_DIR/$(get_env DATABASE_PATH)" "/var/backups/sonarx/pre-update-$(date +%Y%m%d-%H%M%S).sqlite3"; then
      systemctl restart "$SERVICE" || true
      fail "SQLite backup failed; update cancelled."
      return 1
    fi
  fi
  if ! { git -C "$APP_DIR" fetch origin "$BRANCH" &&
         git -C "$APP_DIR" pull --ff-only origin "$BRANCH" &&
         cd "$APP_DIR" &&
         .venv/bin/pip install -r requirements.txt &&
         .venv/bin/alembic upgrade head; }; then
    warn "Update failed; restoring previous source revision."
    git -C "$APP_DIR" reset --hard "$old_head" || true
    (cd "$APP_DIR" && .venv/bin/pip install -r requirements.txt) || true
    systemctl restart "$SERVICE" || true
    fail "Update did not complete. Check /var/log/sonarx-install.log and database migration state."
    return 1
  fi
  systemctl restart "$SERVICE"
  systemctl enable --now sonarx-maintenance.timer
  ok "SonarX updated."
}
remove_app(){
  need_root
  warn "Uninstall preserves PostgreSQL and securely backs up .env / Fernet / local data."
  prompt answer "Remove application files? Type REMOVE to confirm: "
  [[ "$answer" == "REMOVE" ]] || { info "Cancelled."; return; }
  backup_config
  mkdir -p "$BACKUP_DIR" "$PERSIST_DIR"
  chmod 700 "$BACKUP_DIR" "$PERSIST_DIR"
  if [[ -d "$APP_DIR/data" ]]; then
    local saved_data="$BACKUP_DIR/data-$(date +%Y%m%d-%H%M%S)-$$"
    cp -a "$APP_DIR/data" "$saved_data"
    chmod 700 "$saved_data"
    info "Local database files preserved in $saved_data"
  fi
  systemctl disable --now "$SERVICE" || true
  systemctl disable --now sonarx-maintenance.timer || true
  rm -f /etc/systemd/system/sonarx.service /etc/systemd/system/sonarx-maintenance.service /etc/systemd/system/sonarx-maintenance.timer /usr/local/bin/sonarx
  systemctl daemon-reload
  rm -rf "$APP_DIR"
  ok "Application removed. PostgreSQL retained; credentials kept in $PERSIST_DIR/.env"
}
menu(){
  need_root
  while true; do
    echo
    echo " SonarX — Operations Center"
    echo " 1) Install / Repair"
    echo " 2) Update from GitHub"
    echo " 3) Service status + diagnostics"
    echo " 4) Live logs"
    echo " 5) Change Telegram bot token"
    echo " 6) Change primary admin IDs"
    echo " 7) Open CLI help"
    echo " 8) Remove app (preserve database)"
    echo " 0) Exit"
    prompt choice "Select an option: "
    case "$choice" in
      1) install_app;;
      2) update_app;;
      3) systemctl status "$SERVICE" --no-pager || true; /usr/local/bin/sonarx doctor || true;;
      4) journalctl -u "$SERVICE" -n 150 -f;;
      5) [[ -x /usr/local/bin/sonarx ]] && /usr/local/bin/sonarx token-change || { fail "Install SonarX first."; };;
      6) [[ -x /usr/local/bin/sonarx ]] || { fail "Install SonarX first."; continue; }; prompt ids "New ADMIN_IDS (comma-separated): "; /usr/local/bin/sonarx admins-set "$ids";;
      7) [[ -x /usr/local/bin/sonarx ]] && /usr/local/bin/sonarx --help || { fail "Install SonarX first."; };;
      8) remove_app;;
      0) break;;
      *) warn "Invalid option.";;
    esac
  done
}
if [[ "${SONARX_INSTALL_LIB_ONLY:-0}" != "1" ]]; then
case "${1:-menu}" in
  install) install_app;;
  update) update_app;;
  remove) remove_app;;
  status) need_root;systemctl status "$SERVICE" --no-pager;;
  logs) need_root;journalctl -u "$SERVICE" -n 150 -f;;
  menu) menu;;
  *) echo "Usage: install.sh [menu|install|update|remove|status|logs]";exit 2;;
esac
fi
