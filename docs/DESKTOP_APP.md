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

## V1 Backend Behavior

The dev launcher starts the local FastAPI backend if port `8001` is not already
listening. The packaged desktop shell still expects the backend to be available
locally. A later version can bundle a backend sidecar or point to a private
cloud backend.
