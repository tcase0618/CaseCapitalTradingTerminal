#!/usr/bin/env bash
# Install/update service hardening and the Postgres backup timer after a deploy.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/case-capital/stock-intel}"
APP_USER="${APP_USER:-casecapital}"

[[ "${EUID}" -eq 0 ]] || { echo "Run as root" >&2; exit 2; }
install -d -o "${APP_USER}" -g "${APP_USER}" -m 700 /opt/case-capital/backups/postgres
chmod 755 "${APP_DIR}/deploy/vps/backup-postgres.sh" "${APP_DIR}/deploy/vps/restore-postgres.sh"
install -o root -g root -m 644 "${APP_DIR}/deploy/vps/case-capital-terminal.service" /etc/systemd/system/case-capital-terminal.service
install -o root -g root -m 644 "${APP_DIR}/deploy/vps/case-capital-postgres-backup.service" /etc/systemd/system/case-capital-postgres-backup.service
install -o root -g root -m 644 "${APP_DIR}/deploy/vps/case-capital-postgres-backup.timer" /etc/systemd/system/case-capital-postgres-backup.timer
systemctl daemon-reload
systemctl enable --now case-capital-postgres-backup.timer
