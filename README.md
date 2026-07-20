# Stock Intel

Portable stock intelligence dashboard with a FastAPI backend, MongoDB storage,
Telegram delivery hooks, and a React frontend.

## Quick Start With Docker

This is the recommended local path on Windows because it runs the app and MongoDB
the same way you would run them in the cloud.

1. Install Docker Desktop.
2. Copy `backend/.env.example` to `backend/.env`.
3. Fill in any keys you want to use. `ANTHROPIC_API_KEY` powers Claude analysis;
   Telegram and Alpaca keys are optional.
4. From this folder, run:

```powershell
.\start-local.ps1
```

Open the app at:

```text
http://localhost:3000
```

Backend API:

```text
http://127.0.0.1:8001/api/status
```

## Manual Local Run

Run MongoDB locally or use MongoDB Atlas, then set `MONGO_URL` in
`backend/.env`.

Backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

Frontend:

```powershell
cd frontend
Copy-Item .env.example .env
yarn install
yarn start
```

For local frontend development, `frontend/.env` should contain:

```text
REACT_APP_BACKEND_URL=http://127.0.0.1:8001
```

For a same-domain cloud deployment behind a proxy, leave
`REACT_APP_BACKEND_URL` empty so the frontend calls `/api`.

## Required Environment

Backend:

- `MONGO_URL`: MongoDB connection string.
- `DB_NAME`: Mongo database name.
- `ANTHROPIC_API_KEY`: Required for new Claude analysis.

Optional integrations:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `APCA_API_KEY_ID`
- `APCA_API_SECRET_KEY`
- `APCA_API_BASE_URL`
- `FINNHUB_API_KEY`
- `MASSIVE_API_KEY`
- `FRED_API_KEY`
- `FINANCE_NEWS_API_KEY`
- `ALPHA_VANTAGE_API_KEY`

## Trading Safety Notes

PM-managed Trade Floor orders must follow the guardrails in
`docs/PM_ORDER_GUARDRAILS.md`. In short: do not submit buy limits directly from
a raw Alpaca ask when that ask is above the Portfolio Manager entry band.

## Cloud Path

Use the Dockerfiles in this repo:

- Backend service: build `backend/Dockerfile`, expose port `8001`, set the same
  backend environment variables.
- Frontend service: build `frontend/Dockerfile`, expose port `80`. The included
  nginx config serves the React build and proxies `/api` to the backend service.
- Database: use managed MongoDB, such as MongoDB Atlas, and set `MONGO_URL`.

If your cloud provider runs frontend and backend on different domains, set
`REACT_APP_BACKEND_URL` during the frontend build to the public backend URL and
set backend `CORS_ORIGINS` to the frontend origin.

## Notes

- The app no longer depends on Emergent runtime packages, scripts, or keys.
- The Docker path keeps secrets out of git by reading `backend/.env`.
- Trading execution is paper-mode by default when `APCA_API_BASE_URL` is not set.
