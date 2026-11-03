#!/usr/bin/env bash
set -Eeuo pipefail
REPO_URL="${SONARX_REPO_URL:-https://github.com/amirtn9/sonarx.git}"
BRANCH="${SONARX_BRANCH:-main}"
APP_DIR=/opt/sonarx
SERVICE=sonarx
LOG=/var/log/sonarx-install.log
mkdir -p "$(dirname "$LOG")";exec > >(tee -a "$LOG") 2>&1
need_root(){ [[ $EUID -eq 0 ]] || { echo 'Run with sudo/root.';exit 1; }; }
set_env(){ "$APP_DIR/.venv/bin/python" - "$APP_DIR/.env" "$1" "$2" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]);k=sys.argv[2];v=sys.argv[3]
lines=p.read_text().splitlines() if p.exists() else []
lines=[x for x in lines if not x.startswith(k+'=')];lines.append(k+'='+v);p.write_text('\n'.join(lines)+'\n')
PY
}
get_env(){ "$APP_DIR/.venv/bin/python" - "$APP_DIR/.env" "$1" <<'PY'
import sys
from dotenv import dotenv_values
print(dotenv_values(sys.argv[1]).get(sys.argv[2]) or '')
PY
}
install_deps(){
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates openssl python3 python3-venv python3-dev build-essential libpq-dev postgresql postgresql-contrib postgresql-client
  python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required (Ubuntu 24.04 recommended)"'
  systemctl enable --now postgresql
}
fetch_source(){
  if [[ -d "$APP_DIR/.git" ]];then git -C "$APP_DIR" fetch origin;git -C "$APP_DIR" checkout "$BRANCH";git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
  else [[ ! -e "$APP_DIR" ]] || { echo "$APP_DIR exists but is not a git repository";exit 1; };git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR";fi
}
setup_pg(){
  local pass;pass="$(openssl rand -hex 24)"
  if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='sonarx'" | grep -q 1;then runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 -c "ALTER ROLE sonarx WITH LOGIN PASSWORD '$pass'" >/dev/null
  else runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 -c "CREATE ROLE sonarx LOGIN PASSWORD '$pass'" >/dev/null;fi
  if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='sonarx'" | grep -q 1;then runuser -u postgres -- createdb -O sonarx sonarx >/dev/null
  else runuser -u postgres -- psql -q -c 'ALTER DATABASE sonarx OWNER TO sonarx' >/dev/null;fi
  printf '%s' "$pass"
}
configure(){
  local migrated=0 dburl key
  if [[ ! -f "$APP_DIR/.env" ]];then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    read -rp 'Telegram BOT_TOKEN: ' token;read -rp 'Telegram ADMIN_IDS (comma separated): ' admins
    [[ -n "$token" && -n "$admins" ]] || { echo 'BOT_TOKEN and ADMIN_IDS are required';exit 1; }
    set_env BOT_TOKEN "$token";set_env ADMIN_IDS "$admins"
  else echo 'Preserving existing .env';fi
  dburl="$(get_env DATABASE_URL)"
  if [[ -z "$dburl" ]];then
    local pass;pass="$(setup_pg)";dburl="postgresql+psycopg://sonarx:${pass}@127.0.0.1:5432/sonarx";set_env DATABASE_URL "$dburl";set_env POSTGRES_PASSWORD "$pass";migrated=1
  fi
  key="$(get_env FERNET_KEY)"
  if [[ -z "$key" || "$key" == 'generate_with_python_cryptography' ]];then key="$(.venv/bin/python -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')";set_env FERNET_KEY "$key";fi
  chmod 600 "$APP_DIR/.env"
  echo "$migrated" > "$APP_DIR/.migration-needed"
}
install_app(){
  need_root;install_deps;fetch_source;cd "$APP_DIR"
  python3 -m venv .venv;.venv/bin/pip install --upgrade pip wheel;.venv/bin/pip install -r requirements.txt
  configure
  id sonarx >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin sonarx
  mkdir -p data logs;chown -R root:sonarx "$APP_DIR";chown -R sonarx:sonarx data logs;chmod 640 .env
  install -m 0644 systemd/sonarx.service /etc/systemd/system/sonarx.service
  cat >/usr/local/bin/sonarx <<'EOF'
#!/usr/bin/env bash
cd /opt/sonarx
exec /opt/sonarx/.venv/bin/python -m sonarx.cli "$@"
EOF
  chmod 755 /usr/local/bin/sonarx
  .venv/bin/alembic upgrade head
  if [[ "$(cat .migration-needed)" == 1 ]];then .venv/bin/python scripts/migrate_sqlite_to_postgres.py --sqlite "$(get_env DATABASE_PATH)";fi
  rm -f .migration-needed
  systemctl daemon-reload;systemctl enable --now "$SERVICE"
  echo 'SonarX installed. Use: sonarx status'
}
update_app(){ need_root;[[ -d "$APP_DIR/.git" ]] || { echo 'Not installed';exit 1; };systemctl stop "$SERVICE" || true;git -C "$APP_DIR" pull --ff-only;cd "$APP_DIR";.venv/bin/pip install -r requirements.txt;.venv/bin/alembic upgrade head;systemctl start "$SERVICE"; }
remove_app(){ need_root;read -rp 'Remove app files and service, keeping PostgreSQL data? [y/N] ' x;[[ "$x" =~ ^[Yy]$ ]] || exit 0;systemctl disable --now "$SERVICE" || true;rm -f /etc/systemd/system/sonarx.service /usr/local/bin/sonarx;systemctl daemon-reload;rm -rf "$APP_DIR";echo 'App removed; database and PostgreSQL role preserved.'; }
case "${1:-menu}" in
 install)install_app;;update)update_app;;remove)remove_app;;status)need_root;systemctl status "$SERVICE" --no-pager;;logs)need_root;journalctl -u "$SERVICE" -n 100 -f;;menu)
  echo '1) Install  2) Update  3) Status  4) Logs  5) Remove app (keep database)';read -rp 'Select: ' x;case "$x" in 1)install_app;;2)update_app;;3)need_root;systemctl status "$SERVICE" --no-pager;;4)need_root;journalctl -u "$SERVICE" -n 100 -f;;5)remove_app;;*)exit 2;;esac;;*)echo 'Usage: install.sh [install|update|remove|status|logs]';exit 2;;
esac
