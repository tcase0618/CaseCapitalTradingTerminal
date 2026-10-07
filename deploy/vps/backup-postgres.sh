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

trap 'rm -f "${tmp}"' EXIT
pg_dump --format=custom --no-owner --file="${tmp}" "${POSTGRES_DSN}"
pg_restore --list "${tmp}" >/dev/null
sha256sum "${tmp}" > "${tmp}.sha256"
mv "${tmp}" "${final}"
mv "${tmp}.sha256" "${final}.sha256"
trap - EXIT

# Only prune artifacts this job owns. Never glob unrelated application data.
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'case-capital-*.dump' -mtime "+${RETENTION_DAYS}" -print -delete
find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'case-capital-*.dump.sha256' -mtime "+${RETENTION_DAYS}" -print -delete
echo "Backup verified: ${final}"
