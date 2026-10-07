#!/usr/bin/env bash
# Encrypted-at-rest must be handled by the VPS volume provider. This script
# creates a private, checksummed logical backup and verifies that pg_restore
# can read it before treating it as usable.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/case-capital/stock-intel}"
ENV_FILE="${ENV_FILE:-${APP_DIR}/backend/.env}"
BACKUP_DIR="${CASE_CAPITAL_BACKUP_DIR:-/opt/case-capital/backups/postgres}"
RETENTION_DAYS="${CASE_CAPITAL_BACKUP_RETENTION_DAYS:-14}"

if [[ ! -r "${ENV_FILE}" ]]; then
  echo "Missing readable environment file: ${ENV_FILE}" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

if [[ -z "${POSTGRES_DSN:-}" ]]; then
  echo "POSTGRES_DSN is empty; refusing backup" >&2
  exit 2
fi

umask 077
install -d -m 700 "${BACKUP_DIR}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="${BACKUP_DIR}/.case-capital-${stamp}.dump.tmp"
final="${BACKUP_DIR}/case-capital-${stamp}.dump"

trap 'rm -f "${tmp}"; unset PGPASSWORD' EXIT

# Do not pass a DSN containing a password as a process argument. Root can read
# command lines, so hand libpq its password through the process environment.
# It is removed immediately after pg_dump exits and never written to disk.
eval "$(python3 - "${POSTGRES_DSN}" <<'PY'
from shlex import quote
from urllib.parse import unquote, urlparse
import sys

parsed = urlparse(sys.argv[1])
if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.username:
    raise SystemExit("POSTGRES_DSN must be a PostgreSQL URL with host and username")
database = parsed.path.lstrip("/")
if not database:
    raise SystemExit("POSTGRES_DSN is missing database name")
host = parsed.hostname
port = parsed.port or 5432
user = unquote(parsed.username)
password = unquote(parsed.password or "")
print(
    f"export PGHOST={quote(host)} PGPORT={quote(str(port))} PGUSER={quote(user)} "
    f"PGDATABASE={quote(database)} PGPASSWORD={quote(password)}"
)
PY
)"
export PGSSLMODE="${CASE_CAPITAL_POSTGRES_SSLMODE:-disable}"
pg_dump --format=custom --no-owner --file="${tmp}"
pg_restore --list "${tmp}" >/dev/null
mv "${tmp}" "${final}"
sha256sum "${final}" > "${final}.sha256"
trap - EXIT

# Only prune artifacts this job owns. Never glob unrelated application data.
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'case-capital-*.dump' -mtime "+${RETENTION_DAYS}" -print -delete
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'case-capital-*.dump.sha256' -mtime "+${RETENTION_DAYS}" -print -delete
echo "Backup verified: ${final}"
