# CaseCapitalTradingTerminal Desktop

The desktop app is a Tauri shell around the existing React frontend.

## App

- Name: `CaseCapitalTradingTerminal`
- Identifier: `com.casecapital.tradingterminal`
- Frontend: React/CRACO build from `frontend/build`
- Backend V1: local FastAPI process at `http://127.0.0.1:8001`

## Commands

From the repo root:

```powershell
.\start-desktop-dev.ps1
.\build-desktop.ps1
```

The root scripts load the Visual Studio Build Tools environment and force the
matching x64 Rust toolchain/target. This matters on Windows ARM64 machines.

Direct frontend commands are still available after the build environment is
loaded:

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

Verified on this machine:

- Rust: `1.97.1`
- Visual Studio Build Tools 2022: `17.14.36`
- Tauri CLI: `2.11.4`

## Build Output

The verified x64 packages are generated at:

```text
frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/msi/CaseCapitalTradingTerminal_0.1.0_x64_en-US.msi
frontend/src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/CaseCapitalTradingTerminal_0.1.0_x64-setup.exe
```

## Backend Boot Behavior

The dev launcher and packaged desktop app both check `127.0.0.1:8001`.

If the backend is not already listening, the desktop app starts:

```text
backend/.venv/Scripts/python.exe -m uvicorn server:app --host 127.0.0.1 --port 8001
```

The app looks for the backend in this order:

- `CASE_CAPITAL_BACKEND_DIR`, when set
- common repo-relative paths
- `C:\Case Capital\stock-intel\backend`

For the current local desktop build, keep the repo folder and backend virtualenv
on disk. A later public installer should bundle a backend sidecar or point to a
private cloud backend.
