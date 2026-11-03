#!/usr/bin/env bash
set -Eeuo pipefail
REPO_URL="${SONARX_REPO_URL:-https://github.com/Amirtn9/sonarx.git}"
BRANCH="${SONARX_BRANCH:-main}"
APP_DIR="${SONARX_APP_DIR:-/opt/sonarx}"
SERVICE="sonarx"
PERSIST_DIR="${SONARX_PERSIST_DIR:-/var/lib/sonarx}"
BACKUP_DIR="${SONARX_BACKUP_DIR:-/var/backups/sonarx}"
LOG_FILE="${SONARX_LOG_FILE:-/var/log/sonarx-install.log}"
TTY_UI=0
[[ -t 1 ]] && TTY_UI=1
if [[ "${SONARX_INSTALL_LIB_ONLY:-0}" != "1" ]]; then
  mkdir -p "$(dirname "$LOG_FILE")"
  exec > >(tee -a "$LOG_FILE") 2>&1
fi

c(){
  local color="$1"; shift
  if [[ "$TTY_UI" == 1 && -z "${NO_COLOR:-}" ]]; then
    printf '\033[%sm%s\033[0m\n' "$color" "$*"
  else
    printf '%s\n' "$*"
  fi
}
ok(){ c 32 "[OK] $*"; }
info(){ c 36 "[INFO] $*"; }
warn(){ c 33 "[WARN] $*"; }
fail(){ c 31 "[ERROR] $*"; }
rule(){ printf '%s\n' '----------------------------------------------------------------------------'; }
banner(){
  local version="6.3.3"
  [[ -f "$APP_DIR/VERSION" ]] && version="$(tr -d '[:space:]' < "$APP_DIR/VERSION")"
  printf '\n'
  c 36 '+----------------------------------------------------------------------------+'
  c 36 '|                           SONARX CONTROL CENTER                            |'
  c 36 "|                       Telegram VPN Store - v${version}                       |"
  c 36 '+----------------------------------------------------------------------------+'
}
need_root(){ [[ "${EUID}" -eq 0 ]] || { fail "Run as root: sudo bash install.sh"; exit 1; }; }
prompt(){ local __var="$1" __label="$2" __secret="${3:-0}" __value=""; [[ -r /dev/tty ]] || { fail "Interactive terminal required for this step."; exit 1; }; if [[ "$__secret" == 1 ]]; then read -r -s -p "$__label" __value </dev/tty; echo; else read -r -p "$__label" __value </dev/tty; fi; printf -v "$__var" '%s' "$__value"; }

read_env_value(){
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 0
  python3 - "$file" "$key" <<'PY'
from pathlib import Path
import sys
path, wanted = Path(sys.argv[1]), sys.argv[2]
for raw in path.read_text(errors='replace').splitlines():
    line = raw.strip()
    if line.startswith('export '):
        line = line[7:].lstrip()
    name, sep, value = line.partition('=')
    if sep and name.strip() == wanted:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        print(value)
        break
PY
}

# Return only a project-specific canonical directory. Refuse root/system paths
# and paths that could contain unrelated applications or the whole server.
safe_sonarx_dir(){
  local requested="$1" resolved base
  resolved="$(realpath -m -- "$requested")" || return 1
  case "$resolved" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|/var/lib|/var/backups|/var/log)
      fail "Refusing to remove a system directory: $resolved"; return 1;;
  esac
  base="$(basename -- "$resolved")"
  [[ "$base" == sonarx* ]] || {
    fail "Unsafe purge path (must be a dedicated sonarx* directory): $requested"; return 1;
  }
  printf '%s\n' "$resolved"
}

# Print: local<TAB>database<TAB>role, remote<TAB>host<TAB>database<TAB>role,
# or unknown. Only local SonarX-owned PostgreSQL databases are purgeable here.
purge_database_target(){
  local env_file="$APP_DIR/.env" url
  [[ -f "$env_file" ]] || env_file="$PERSIST_DIR/.env"
  url="$(read_env_value "$env_file" DATABASE_URL)"
  if [[ -z "$url" ]]; then
    local sqlite_path sqlite_real app_real persist_real sqlite_base
    sqlite_path="$(read_env_value "$env_file" DATABASE_PATH)"
    if [[ -n "$sqlite_path" ]]; then
      [[ "$sqlite_path" == /* ]] || sqlite_path="$APP_DIR/$sqlite_path"
      sqlite_real="$(realpath -m -- "$sqlite_path")"
      app_real="$(realpath -m -- "$APP_DIR")"
      persist_real="$(realpath -m -- "$PERSIST_DIR")"
      sqlite_base="$(basename -- "$sqlite_real")"
      case "$sqlite_real" in
        "$app_real"/*|"$persist_real"/*) printf 'sqlite\t%s\n' "$sqlite_real"; return 0;;
      esac
      if [[ "$sqlite_base" == sonarx*.sqlite* || "$sqlite_base" == sonarx*.db ]]; then
        printf 'sqlite\t%s\n' "$sqlite_real"
        return 0
      fi
      printf 'unknown\n'
      return 0
    fi
    printf 'local\tsonarx\tsonarx\n'
    return 0
  fi
  python3 - "$url" <<'PY'
import sys
from urllib.parse import urlsplit, unquote
url = sys.argv[1].strip()
try:
    parsed = urlsplit(url)
    if not parsed.scheme.startswith('postgres'):
        print('unknown')
        raise SystemExit(0)
    host = (parsed.hostname or '').lower()
    database = unquote(parsed.path.lstrip('/'))
    role = unquote(parsed.username or '')
    if host in ('', 'localhost', '127.0.0.1', '::1'):
        print('local\t{}\t{}'.format(database, role or 'sonarx'))
    else:
        print('remote\t{}\t{}\t{}'.format(host, database, role or ''))
except Exception:
    print('unknown')
PY
}

verify_runtime_source(){
  "$APP_DIR/.venv/bin/python" - <<'PY'
import ast
from pathlib import Path
source = Path('sonarx/bot.py').read_text(encoding='utf-8')
tree = ast.parse(source)
functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
missing = {'maintenance', 'feature'} - functions
if missing:
    raise SystemExit('Unsafe SonarX source: missing required function(s): ' + ', '.join(sorted(missing)))
print('Runtime source check: maintenance guard and feature helper are present.')
PY
}
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
CURRENCY_LABEL=Toman
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
  # Prevent the bot and its maintenance worker from using files while they
  # are being repaired or migrated.
  systemctl stop sonarx-maintenance.timer sonarx-maintenance.service 2>/dev/null || true
  install_deps
  fetch_source
  cd "$APP_DIR"
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip wheel
  .venv/bin/pip install -r requirements.txt
  verify_runtime_source
  configure
  systemctl stop "$SERVICE" 2>/dev/null || true
  id sonarx >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin sonarx
  mkdir -p data logs
  chown -R root:sonarx "$APP_DIR"
  chown -R sonarx:sonarx data logs
  chmod 640 .env
  python3 - "$APP_DIR" systemd/sonarx.service /etc/systemd/system/sonarx.service <<'PY'
from pathlib import Path
import sys
root, source, target = sys.argv[1:]
Path(target).write_text(Path(source).read_text().replace('/opt/sonarx', root))
PY
  python3 - "$APP_DIR" systemd/sonarx-maintenance.service /etc/systemd/system/sonarx-maintenance.service <<'PY'
from pathlib import Path
import sys
root, source, target = sys.argv[1:]
Path(target).write_text(Path(source).read_text().replace('/opt/sonarx', root))
PY
  install -m 0644 systemd/sonarx-maintenance.timer /etc/systemd/system/sonarx-maintenance.timer
  cat >/usr/local/bin/sonarx <<WRAPPER
#!/usr/bin/env bash
export SONARX_APP_DIR="$APP_DIR"
cd "$APP_DIR"
exec "$APP_DIR/.venv/bin/python" -m sonarx.cli "\$@"
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
  systemctl stop sonarx-maintenance.timer sonarx-maintenance.service 2>/dev/null || true
  systemctl stop "$SERVICE" || true
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"
  if [[ "$(get_env DATABASE_URL)" == postgresql* ]]; then
    if ! /usr/local/bin/sonarx backup --output "$BACKUP_DIR/pre-update-$(date +%Y%m%d-%H%M%S).dump"; then
      systemctl restart "$SERVICE" || true
      systemctl enable --now sonarx-maintenance.timer 2>/dev/null || true
      fail "Pre-update database backup failed; update cancelled."
      return 1
    fi
  elif [[ -n "$(get_env DATABASE_PATH)" && -f "$APP_DIR/$(get_env DATABASE_PATH)" ]]; then
    if ! cp "$APP_DIR/$(get_env DATABASE_PATH)" "$BACKUP_DIR/pre-update-$(date +%Y%m%d-%H%M%S).sqlite3"; then
      systemctl restart "$SERVICE" || true
      systemctl enable --now sonarx-maintenance.timer 2>/dev/null || true
      fail "SQLite backup failed; update cancelled."
      return 1
    fi
  fi
  if ! { git -C "$APP_DIR" fetch origin "$BRANCH" &&
         git -C "$APP_DIR" pull --ff-only origin "$BRANCH" &&
         cd "$APP_DIR" &&
         .venv/bin/pip install -r requirements.txt &&
         verify_runtime_source &&
         .venv/bin/alembic upgrade head; }; then
    warn "Update failed; restoring previous source revision."
    git -C "$APP_DIR" reset --hard "$old_head" || true
    (cd "$APP_DIR" && .venv/bin/pip install -r requirements.txt) || true
    systemctl restart "$SERVICE" || true
    systemctl enable --now sonarx-maintenance.timer 2>/dev/null || true
    fail "Update did not complete. Check /var/log/sonarx-install.log and database migration state."
    return 1
  fi
  systemctl restart "$SERVICE"
  systemctl enable --now sonarx-maintenance.timer
  ok "SonarX updated."
}
remove_app(){
  need_root
  local answer
  warn "This removes application files only. PostgreSQL, database, backups and credentials are preserved."
  prompt answer "To remove application files, type REMOVE: "
  [[ "$answer" == "REMOVE" ]] || { info "Cancelled."; return 0; }
  backup_config
  mkdir -p "$BACKUP_DIR" "$PERSIST_DIR"
  chmod 700 "$BACKUP_DIR" "$PERSIST_DIR"
  if [[ -d "$APP_DIR/data" ]]; then
    local saved_data="$BACKUP_DIR/data-$(date +%Y%m%d-%H%M%S)-$$"
    cp -a "$APP_DIR/data" "$saved_data"
    chmod 700 "$saved_data"
    info "Local database files preserved in $saved_data"
  fi
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  systemctl disable --now sonarx-maintenance.timer 2>/dev/null || true
  systemctl stop sonarx-maintenance.service 2>/dev/null || true
  rm -f /etc/systemd/system/sonarx.service /etc/systemd/system/sonarx-maintenance.service /etc/systemd/system/sonarx-maintenance.timer /usr/local/bin/sonarx
  rm -rf /etc/systemd/system/sonarx.service.d /etc/systemd/system/sonarx-maintenance.service.d /etc/systemd/system/sonarx-maintenance.timer.d
  systemctl daemon-reload
  rm -rf -- "$APP_DIR"
  ok "Application files removed. PostgreSQL and all preserved data remain intact."
  info "Configuration backup: $PERSIST_DIR/.env"
  info "Data/backups: $BACKUP_DIR"
}

full_purge(){
  need_root
  local app_real persist_real backup_real log_base db_target db_scope db_rest db_name db_role db_confirm
  local answer confirm_db sqlite_path env_file remote_host

  app_real="$(safe_sonarx_dir "$APP_DIR")" || return 1
  persist_real="$(safe_sonarx_dir "$PERSIST_DIR")" || return 1
  backup_real="$(safe_sonarx_dir "$BACKUP_DIR")" || return 1

  # Overlapping targets could make one rm -rf consume another path unexpectedly.
  if [[ "$app_real" == "$persist_real" || "$app_real/" == "$persist_real/"* ||
        "$persist_real/" == "$app_real/"* || "$app_real" == "$backup_real" ||
        "$app_real/" == "$backup_real/"* || "$backup_real/" == "$app_real/"* ||
        "$persist_real" == "$backup_real" || "$persist_real/" == "$backup_real/"* ||
        "$backup_real/" == "$persist_real/"* ]]; then
    fail "Purge paths overlap. Refusing to continue."; return 1
  fi

  db_target="$(purge_database_target)" || { fail "Could not inspect DATABASE_URL."; return 1; }
  db_scope="${db_target%%$'\t'*}"
  db_rest="${db_target#*$'\t'}"
  if [[ "$db_scope" == "sqlite" ]]; then
    db_name="SQLite file: ${db_rest}"
    db_role=""
  elif [[ "$db_scope" == "remote" ]]; then
    remote_host="${db_rest%%$'\t'*}"
    fail "DATABASE_URL points to remote PostgreSQL ($remote_host). No files were removed. Delete that database separately or configure a local SonarX database first."
    return 1
  fi
  if [[ "$db_scope" != "local" && "$db_scope" != "sqlite" ]]; then
    fail "DATABASE_URL is missing or not a supported local database URL. No files were removed."
    return 1
  fi
  if [[ "$db_scope" == "local" ]]; then
    IFS=$'\t' read -r db_name db_role <<< "$db_rest"
    [[ -n "$db_name" ]] || { fail "The configured PostgreSQL database name is empty. No files were removed."; return 1; }
    [[ "$db_name" =~ ^sonarx([_-][A-Za-z0-9_-]+)?$ ]] || {
      fail "Refusing to drop database '$db_name': only a SonarX-named local database can be removed automatically."; return 1;
    }
  fi
  db_confirm="$db_name"
  [[ "$db_scope" == "sqlite" ]] && db_confirm="SONARX SQLITE"
  if [[ -n "$db_role" && ! "$db_role" =~ ^sonarx([_-][A-Za-z0-9_-]+)?$ ]]; then
    warn "PostgreSQL role '$db_role' is not SonarX-named; the database will be dropped but this shared/custom role will be retained."
  fi

  printf '\n'
  c 31 '+----------------------------------------------------------------------------+'
  c 31 '|                         WARNING: FULL DATA PURGE                           |'
  c 31 '+----------------------------------------------------------------------------+'
  warn "This action is irreversible. It will remove:"
  printf '  - Application/source: %s\n' "$app_real"
  printf '  - Persistent config:  %s\n' "$persist_real"
  printf '  - Backups:            %s\n' "$backup_real"
  printf '  - Installer log:      %s\n' "$LOG_FILE"
  if [[ "$db_scope" == "local" ]]; then printf '  - PostgreSQL DB:      %s (local only)\n' "$db_name"; else printf '  - SQLite DB:          %s\n' "$db_name"; fi
  if [[ "$db_role" == sonarx* ]]; then printf '  - PostgreSQL role:    %s\n' "$db_role"; fi
  printf '  - systemd units, CLI wrapper, and dedicated sonarx OS user\n'
  printf '\n'
  warn "The PostgreSQL server package and other databases will NOT be removed."
  prompt answer "To continue, type exactly PURGE SONARX: "
  [[ "$answer" == "PURGE SONARX" ]] || { info "Cancelled. Nothing was removed."; return 0; }
  prompt confirm_db "For the final confirmation, type '$db_confirm': "
  [[ "$confirm_db" == "$db_confirm" ]] || { info "Final confirmation did not match. Nothing was removed."; return 0; }

  if [[ "$db_scope" == "local" ]] && { ! command -v runuser >/dev/null 2>&1 || ! command -v dropdb >/dev/null 2>&1; }; then
    fail "PostgreSQL administration tools are unavailable. Nothing was removed; install/repair PostgreSQL tools and retry."
    return 1
  fi

  info "Stopping SonarX services..."
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  systemctl disable --now sonarx-maintenance.timer 2>/dev/null || true
  systemctl stop sonarx-maintenance.service 2>/dev/null || true

  if [[ "$db_scope" == "local" ]]; then
    if ! systemctl is-active --quiet postgresql; then
      if ! systemctl start postgresql; then
        fail "Could not start local PostgreSQL. Project files were not removed."
        systemctl restart "$SERVICE" 2>/dev/null || true
        systemctl enable --now sonarx-maintenance.timer 2>/dev/null || true
        return 1
      fi
    fi
    info "Dropping local SonarX database '$db_name'..."
    if ! runuser -u postgres -- dropdb --if-exists --force "$db_name"; then
      fail "Could not drop database '$db_name'. Project files were not removed; fix PostgreSQL and retry."
      systemctl restart "$SERVICE" 2>/dev/null || true
      systemctl enable --now sonarx-maintenance.timer 2>/dev/null || true
      return 1
    fi
    if [[ "$db_role" == sonarx* ]]; then
      if runuser -u postgres -- dropuser --if-exists "$db_role"; then
        ok "PostgreSQL role '$db_role' removed."
      else
        warn "PostgreSQL role '$db_role' still has dependencies and was retained. Other databases were not modified."
      fi
    fi
  else
    info "SQLite data will be removed with the configured SonarX files."
  fi

  # Remove an explicitly configured external SQLite file only when its filename
  # clearly belongs to SonarX. Paths inside APP_DIR are removed with APP_DIR.
  env_file="$APP_DIR/.env"
  [[ -f "$env_file" ]] || env_file="$PERSIST_DIR/.env"
  sqlite_path="$(read_env_value "$env_file" DATABASE_PATH)"
  if [[ -n "$sqlite_path" ]]; then
    [[ "$sqlite_path" == /* ]] || sqlite_path="$APP_DIR/$sqlite_path"
    sqlite_path="$(realpath -m -- "$sqlite_path")"
    case "$sqlite_path" in
      "$app_real"/*|"$persist_real"/*) : ;;
      *)
        if [[ "$(basename -- "$sqlite_path")" == sonarx*.sqlite* || "$(basename -- "$sqlite_path")" == sonarx*.db ]]; then
          rm -f -- "$sqlite_path" "$sqlite_path-wal" "$sqlite_path-shm"
        else
          warn "External SQLite path was not removed because its filename is not SonarX-specific: $sqlite_path"
        fi;;
    esac
  fi

  info "Removing systemd units and CLI wrapper..."
  rm -f /etc/systemd/system/sonarx.service /etc/systemd/system/sonarx-maintenance.service /etc/systemd/system/sonarx-maintenance.timer /usr/local/bin/sonarx
  rm -rf /etc/systemd/system/sonarx.service.d /etc/systemd/system/sonarx-maintenance.service.d /etc/systemd/system/sonarx-maintenance.timer.d
  systemctl daemon-reload
  systemctl reset-failed sonarx.service sonarx-maintenance.service sonarx-maintenance.timer 2>/dev/null || true
  if id sonarx >/dev/null 2>&1; then userdel sonarx 2>/dev/null || warn "Could not remove OS user 'sonarx'; inspect it manually."; fi

  info "Removing SonarX source, config, backups and installer log..."
  rm -rf -- "$app_real" "$persist_real" "$backup_real"
  log_base="$(basename -- "$LOG_FILE")"
  if [[ "$log_base" == sonarx-install.log || "$log_base" == sonarx-install-*.log ]]; then
    rm -f -- "$LOG_FILE"
  else
    warn "Custom installer log was retained because its filename is not SonarX-specific: $LOG_FILE"
  fi
  ok "SonarX full purge completed."
  info "Local SonarX database and project files were removed. PostgreSQL and unrelated databases remain."
  info "Historical entries in the shared systemd journal are retained; deleting them would affect other services."
  exit 0
}

show_status(){
  local app_state service_state timer_state pg_state db_state
  [[ -d "$APP_DIR" ]] && app_state="Installed" || app_state="Not found"
  service_state="$(systemctl is-active "$SERVICE" 2>/dev/null || true)"; [[ -n "$service_state" ]] || service_state="Not installed"
  timer_state="$(systemctl is-active sonarx-maintenance.timer 2>/dev/null || true)"; [[ -n "$timer_state" ]] || timer_state="Not installed"
  pg_state="$(systemctl is-active postgresql 2>/dev/null || true)"; [[ -n "$pg_state" ]] || pg_state="Not installed"
  db_state="Unknown"
  if command -v runuser >/dev/null 2>&1 && command -v psql >/dev/null 2>&1 && systemctl is-active --quiet postgresql; then
    if runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='sonarx'" 2>/dev/null | grep -q 1; then db_state="Present"; else db_state="Not found"; fi
  fi
  banner
  rule
  printf '  Application path:  %s  (%s)\n' "$APP_DIR" "$app_state"
  printf '  Bot service:       %s\n' "$service_state"
  printf '  Maintenance timer: %s\n' "$timer_state"
  printf '  PostgreSQL:        %s\n' "$pg_state"
  printf '  SonarX database:   %s\n' "$db_state"
  rule
}

menu(){
  need_root
  while true; do
    show_status
    printf '\n'
    c 36 '  +-------------------------- OPERATIONS --------------------------+'
    printf '  |  1  Install / Repair                                             |\n'
    printf '  |  2  Update from GitHub                                          |\n'
    printf '  |  3  System status and diagnostics                               |\n'
    printf '  |  4  Follow live bot logs                                        |\n'
    printf '  |  5  Change Telegram bot token                                   |\n'
    printf '  |  6  Change administrator IDs                                    |\n'
    printf '  |  7  Show command-line help                                      |\n'
    printf '  |  8  Uninstall app (keep database and backups)                   |\n'
    c 31 '  |  9  FULL WIPE: remove app, database, config, and backups        |'
    c 36 '  |  0  Exit                                                        |'
    c 36 '  +----------------------------------------------------------------+'
    prompt choice "Select an option: "
    case "$choice" in
      1) install_app;;
      2) update_app;;
      3) show_status; /usr/local/bin/sonarx doctor 2>/dev/null || true; systemctl status "$SERVICE" --no-pager || true;;
      4) journalctl -u "$SERVICE" -n 150 -f || true;;
      5) [[ -x /usr/local/bin/sonarx ]] && /usr/local/bin/sonarx token-change || { fail "SonarX is not installed yet."; };;
      6) [[ -x /usr/local/bin/sonarx ]] || { fail "SonarX is not installed yet."; continue; }; prompt ids "Enter administrator IDs (comma-separated): "; /usr/local/bin/sonarx admins-set "$ids";;
      7) [[ -x /usr/local/bin/sonarx ]] && /usr/local/bin/sonarx --help || { fail "SonarX is not installed yet."; };;
      8) remove_app;;
      9) full_purge;;
      0) info "Exiting installer."; break;;
      *) warn "Invalid option. Please choose a number from 0 to 9.";;
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
  purge) full_purge;;
  *) echo "Usage: install.sh [menu|install|update|remove|purge|status|logs]";exit 2;;
esac
fi
