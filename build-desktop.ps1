$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontend = Join-Path $root "frontend"
$vsDevCmd = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"

if (!(Test-Path $vsDevCmd)) {
    throw "Visual Studio Build Tools not found. Install MSVC Build Tools, then rerun this script."
}

$command = @"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%" &&
set "RUSTUP_TOOLCHAIN=stable-x86_64-pc-windows-msvc" &&
call "$vsDevCmd" -arch=x64 -host_arch=arm64 &&
cd /d "$frontend" &&
npm run desktop:build -- --target x86_64-pc-windows-msvc
"@ -replace "(`r?`n)+", " "

cmd /c $command
