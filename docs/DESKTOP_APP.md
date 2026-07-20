# CaseCapitalTradingTerminal Desktop

The desktop app is a Tauri shell around the existing React frontend.

## App

- Name: `CaseCapitalTradingTerminal`
- Identifier: `com.casecapital.tradingterminal`
- Frontend: React/CRACO build from `frontend/build`
- Backend V1: local FastAPI process at `http://localhost:8001`

## Commands

From the repo root:

```powershell
.\start-desktop-dev.ps1
```

From `frontend/`:

```powershell
npm run desktop:dev
npm run desktop:build
```

## Required Build Tools

Tauri requires Rust and the Windows C++ build toolchain before a Windows `.exe`
can be produced.

Install:

- Rust via `rustup`
- Microsoft C++ Build Tools / Visual Studio Build Tools
- WebView2 Runtime, normally already installed on modern Windows

## V1 Backend Behavior

The dev launcher starts the local FastAPI backend if port `8001` is not already
listening. The packaged desktop shell still expects the backend to be available
locally. A later version can bundle a backend sidecar or point to a private
cloud backend.
