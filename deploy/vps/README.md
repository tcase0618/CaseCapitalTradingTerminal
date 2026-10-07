# Case Capital VPS Deployment

Target: Ubuntu 24.04 LTS on a small VPS.

## Server shape

- 1 vCPU
- 2 GB RAM
- 50 GB NVMe
- Local PostgreSQL for terminal state and audit history
- Nginx serves React and proxies `/api` to FastAPI
- systemd keeps backend alive

## Safety model

`ENABLE_TRADE_EXECUTION=false` by default. Keep it false until this VPS is the only execution authority. Do not run desktop and cloud with execution enabled at the same time.

## Bootstrap

On the VPS:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/tcase0618/CaseCapitalTradingTerminal.git /opt/case-capital/stock-intel
cd /opt/case-capital/stock-intel
sudo bash deploy/vps/bootstrap-ubuntu-24.sh
```

Then edit:

```bash
sudo nano /opt/case-capital/stock-intel/backend/.env
sudo systemctl restart case-capital-terminal
```

## Postgres operations

Production migrations are explicit so concurrent indexes never run as an
unbounded application-startup side effect:

```bash
cd /opt/case-capital/stock-intel/backend
.venv/bin/python scripts/migrate_postgres.py
```

The deployment installs a nightly, verified custom-format backup at 03:10 ET.
Each dump is checked with `pg_restore --list`, saved with mode `0600`, and kept
for 14 days by default. Verify its timer and latest result with:

```bash
systemctl list-timers case-capital-postgres-backup.timer
systemctl status case-capital-postgres-backup.service --no-pager
```

`deploy/vps/restore-postgres.sh` is deliberately destructive and requires the
explicit `CASE_CAPITAL_RESTORE_CONFIRM=RESTORE_CASE_CAPITAL` confirmation.

Verify:

```bash
curl http://127.0.0.1:8001/api/status
curl http://YOUR_SERVER_IP/api/status
systemctl status case-capital-terminal --no-pager
```

## One-shot update deploy

Use this when the code has already been pushed and you want one clean VPS
update:

```bash
cd /opt/case-capital/stock-intel
git fetch origin
git checkout codex/desktop-checkpoint
git pull origin codex/desktop-checkpoint

cd /opt/case-capital/stock-intel/backend
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/migrate_postgres.py
sudo bash ../deploy/vps/install-operational-controls.sh
sudo systemctl restart case-capital-terminal
sleep 4
.venv/bin/python readiness_check.py

cd /opt/case-capital/stock-intel/frontend
npm install --legacy-peer-deps
npm run build
sudo systemctl restart nginx

curl -s http://127.0.0.1:8001/api/readiness/overview | python3 -m json.tool
curl -s http://127.0.0.1:8001/api/options_desk/marks/audit | python3 -m json.tool
```

The readiness report can return `WATCH` when equity execution is intentionally
off or a source is degraded. Treat `BLOCK` as a stop sign before market open.
