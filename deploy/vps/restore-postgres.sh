#!/usr/bin/env bash
# Destructive recovery helper. This is intentionally not called by deploy.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/case-capital/stock-intel}"
ENV_FILE="${ENV_FILE:-${APP_DIR}/backend/.env}"
BACKUP_FILE="${1:-}"

if [[ "${CASE_CAPITAL_RESTORE_CONFIRM:-}" != "RESTORE_CASE_CAPITAL" ]]; then
  echo "Set CASE_CAPITAL_RESTORE_CONFIRM=RESTORE_CASE_CAPITAL to run a restore." >&2
  exit 2
fi
if [[ -z "${BACKUP_FILE}" || ! -f "${BACKUP_FILE}" ]]; then
  echo "Usage: CASE_CAPITAL_RESTORE_CONFIRM=RESTORE_CASE_CAPITAL $0 /path/to/backup.dump" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a
[[ -n "${POSTGRES_DSN:-}" ]] || { echo "POSTGRES_DSN is empty" >&2; exit 2; }

# pg_restore accepts the deployment DSN here because this recovery command is
# invoked manually, not left running in a process list. Operators should use a
# protected shell and rotate credentials after any incident response.
pg_restore --list "${BACKUP_FILE}" >/dev/null
pg_restore --clean --if-exists --no-owner --dbname="${POSTGRES_DSN}" "${BACKUP_FILE}"
echo "Restore completed from ${BACKUP_FILE}"
