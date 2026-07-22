# Case Capital VPS Deployment

Target: Ubuntu 24.04 LTS on a small VPS.

## Server shape

- 1 vCPU
- 2 GB RAM
- 50 GB NVMe
- MongoDB Atlas for database
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

Verify:

```bash
curl http://127.0.0.1:8001/api/status
curl http://YOUR_SERVER_IP/api/status
systemctl status case-capital-terminal --no-pager
```
