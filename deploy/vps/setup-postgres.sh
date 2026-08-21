#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/case-capital/stock-intel}"
APP_USER="${APP_USER:-casecapital}"
POSTGRES_DB="${POSTGRES_DB:-casecapital}"
POSTGRES_USER="${POSTGRES_USER:-casecapital}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/vps/setup-postgres.sh"
  exit 1
fi

if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then
  echo "POSTGRES_PASSWORD is required."
  echo "Example: POSTGRES_PASSWORD='strong-password' sudo -E bash deploy/vps/setup-postgres.sh"
  exit 2
fi

apt-get update
apt-get install -y --no-install-recommends postgresql postgresql-contrib
systemctl enable postgresql
systemctl restart postgresql

sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
do \$\$
begin
   if not exists (select from pg_catalog.pg_roles where rolname = '${POSTGRES_USER}') then
      create role ${POSTGRES_USER} login password '${POSTGRES_PASSWORD}';
   else
      alter role ${POSTGRES_USER} with password '${POSTGRES_PASSWORD}';
   end if;
end
\$\$;
select 'create database ${POSTGRES_DB} owner ${POSTGRES_USER}'
where not exists (select from pg_database where datname = '${POSTGRES_DB}')\\gexec
grant all privileges on database ${POSTGRES_DB} to ${POSTGRES_USER};
SQL

ENV_FILE="${APP_DIR}/backend/.env"
touch "${ENV_FILE}"
python3 - "$ENV_FILE" "${POSTGRES_DB}" "${POSTGRES_USER}" "${POSTGRES_PASSWORD}" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
db, user, password = sys.argv[2:5]
dsn = f"postgresql://{user}:{password}@127.0.0.1:5432/{db}"
updates = {
    "POSTGRES_ENABLED": "true",
    "POSTGRES_DSN": dsn,
    "POSTGRES_POOL_MAX": "5",
}
lines = env_path.read_text().splitlines() if env_path.exists() else []
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else None
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
env_path.write_text("\n".join(out).rstrip() + "\n")
PY

chown "${APP_USER}:${APP_USER}" "${ENV_FILE}"
chmod 600 "${ENV_FILE}"

sudo -u "${APP_USER}" bash -lc "cd '${APP_DIR}/backend' && . .venv/bin/activate && pip install -r requirements.txt && python - <<'PY'
import asyncio
from dotenv import load_dotenv
load_dotenv('.env')
from services import postgres_store
async def main():
    print(await postgres_store.status())
asyncio.run(main())
PY"

systemctl restart case-capital-terminal
echo "Postgres is installed and Case Capital env is configured."
echo "Check: curl -s http://127.0.0.1:8001/api/postgres/status"
