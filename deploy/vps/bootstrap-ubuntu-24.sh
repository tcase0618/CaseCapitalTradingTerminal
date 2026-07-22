#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/case-capital/stock-intel"
APP_USER="casecapital"
REPO_URL="${REPO_URL:-https://github.com/tcase0618/CaseCapitalTradingTerminal.git}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/vps/bootstrap-ubuntu-24.sh"
  exit 1
fi

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git nginx python3.12 python3.12-venv python3-pip ufw

if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi

id -u "${APP_USER}" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "${APP_USER}"
mkdir -p "$(dirname "${APP_DIR}")"

if [[ ! -d "${APP_DIR}/.git" ]]; then
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" pull --ff-only
fi

chown -R "${APP_USER}:${APP_USER}" /opt/case-capital

if [[ ! -f "${APP_DIR}/backend/.env" ]]; then
  cp "${APP_DIR}/deploy/vps/cloud.env.example" "${APP_DIR}/backend/.env"
  chown "${APP_USER}:${APP_USER}" "${APP_DIR}/backend/.env"
  chmod 600 "${APP_DIR}/backend/.env"
  echo "Created ${APP_DIR}/backend/.env. Fill secrets before starting production traffic."
fi

sudo -u "${APP_USER}" bash -lc "cd '${APP_DIR}/backend' && python3.12 -m venv .venv && . .venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"
sudo -u "${APP_USER}" bash -lc "cd '${APP_DIR}/frontend' && npm ci && REACT_APP_BACKEND_URL= npm run build"

cp "${APP_DIR}/deploy/vps/case-capital-terminal.service" /etc/systemd/system/case-capital-terminal.service
cp "${APP_DIR}/deploy/vps/nginx-case-capital.conf" /etc/nginx/sites-available/case-capital-terminal
ln -sf /etc/nginx/sites-available/case-capital-terminal /etc/nginx/sites-enabled/case-capital-terminal
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable case-capital-terminal
systemctl restart case-capital-terminal
systemctl enable nginx
systemctl restart nginx

ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

echo "Case Capital terminal deployed."
echo "Check backend: systemctl status case-capital-terminal --no-pager"
echo "Logs: journalctl -u case-capital-terminal -f"
