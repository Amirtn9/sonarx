#!/usr/bin/env bash
set -Eeuo pipefail
REPO_URL="${SONARX_REPO_URL:-https://github.com/Amirtn9/sonarx.git}"
BRANCH="${SONARX_BRANCH:-main}"
APP_DIR="/opt/sonarx"
SERVICE="sonarx"
LOG_FILE="/var/log/sonarx-install.log"
mkdir -p "$(dirname "$LOG_FILE")"
exec > >(tee -a "$LOG_FILE") 2>&1

c(){ if [[ -t 1 ]]; then printf '\033[%sm%s\033[0m\n' "$1" "$2"; else printf '%s\n' "$2"; fi; }
ok(){ c 32 "[OK] $*"; }
info(){ c 36 "[INFO] $*"; }
warn(){ c 33 "[WARN] $*"; }
fail(){ c 31 "[ERROR] $*"; }
need_root(){ [[ "${EUID}" -eq 0 ]] || { fail "Run as root: sudo bash install.sh"; exit 1; }; }
prompt(){ local __var="$1" __label="$2" __secret="${3:-0}" __value=""; [[ -r /dev/tty ]] || { fail "Interactive terminal required for this step."; exit 1; }; if [[ "$__secret" == 1 ]]; then read -r -s -p "$__label" __value </dev/tty; echo; else read -r -p "$__label" __value </dev/tty; fi; printf -v "$__var" '%s' "$__value"; }
set_env(){
  "$APP_DIR/.venv/bin/python" - "$APP_DIR/.env" "$1" "$2" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]); k=sys.argv[2]; v=sys.argv[3]
lines=p.read_text().splitlines() if p.exists() else []
lines=[line for line in lines if not line.startswith(k+"=")]
lines.append(k+"="+v)
p.write_text("\n".join(lines)+"\n")
PY
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
    runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 -c "ALTER ROLE sonarx WITH LOGIN PASSWORD '$pass'" >/dev/null
  else
    runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 -c "CREATE ROLE sonarx LOGIN PASSWORD '$pass'" >/dev/null
  fi
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='sonarx'" | grep -q 1; then
    runuser -u postgres -- createdb -O sonarx sonarx >/dev/null
  else
    runuser -u postgres -- psql -q -c "ALTER DATABASE sonarx OWNER TO sonarx" >/dev/null
  fi
  printf '%s' "$pass"
}
configure(){
  local migrated=0 dburl key token admins pass
  if [[ ! -f "$APP_DIR/.env" ]]; then
    if [[ ! -f "$APP_DIR/.env.example" ]]; then
      fail ".env.example is missing from the GitHub release."
      exit 1
    fi
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    prompt token "Telegram BOT_TOKEN (input hidden): " 1
    prompt admins "Telegram ADMIN_IDS (comma-separated numeric IDs): "
    [[ -n "$token" && -n "$admins" ]] || { fail "BOT_TOKEN and ADMIN_IDS are required."; exit 1; }
    "$APP_DIR/.venv/bin/python" - "$token" "$admins" <<'PY'
import re,sys,urllib.request,json
token,ids=sys.argv[1:]
if not re.fullmatch(r'[0-9]{5,15}:[A-Za-z0-9_-]{20,}',token): raise SystemExit("Invalid Telegram token format")
if not all(re.fullmatch(r'[0-9]{4,20}',x.strip()) for x in ids.split(',') if x.strip()): raise SystemExit("ADMIN_IDS must be numeric")
req=urllib.request.Request(f'https://api.telegram.org/bot{token}/getMe')
with urllib.request.urlopen(req,timeout=15) as r: data=json.loads(r.read())
if not data.get('ok'): raise SystemExit("Telegram rejected the token")
print("Validated bot:", "@"+data['result'].get('username',''))
PY
    set_env BOT_TOKEN "$token"
    set_env ADMIN_IDS "$admins"
  else
    warn "Existing .env preserved; secrets will not be overwritten."
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
  ok "SonarX installed. Run: sonarx status"
  info "Admin commands: sonarx token-change | sonarx admins-set ID1,ID2 | sonarx logs -n 200 -f"
}
update_app(){
  need_root
  [[ -d "$APP_DIR/.git" ]] || { fail "SonarX is not installed."; exit 1; }
  systemctl stop "$SERVICE" || true
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
  cd "$APP_DIR"
  .venv/bin/pip install -r requirements.txt
  .venv/bin/alembic upgrade head
  systemctl restart "$SERVICE"
  ok "SonarX updated."
}
remove_app(){
  need_root
  warn "This removes app code/service only. PostgreSQL database and role are preserved."
  prompt answer "Remove application files? Type REMOVE to confirm: "
  [[ "$answer" == "REMOVE" ]] || { info "Cancelled."; return; }
  systemctl disable --now "$SERVICE" || true
  rm -f /etc/systemd/system/sonarx.service /usr/local/bin/sonarx
  systemctl daemon-reload
  rm -rf "$APP_DIR"
  ok "Application removed; database preserved."
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
case "${1:-menu}" in
  install) install_app;;
  update) update_app;;
  remove) remove_app;;
  status) need_root;systemctl status "$SERVICE" --no-pager;;
  logs) need_root;journalctl -u "$SERVICE" -n 150 -f;;
  menu) menu;;
  *) echo "Usage: install.sh [menu|install|update|remove|status|logs]";exit 2;;
esac
