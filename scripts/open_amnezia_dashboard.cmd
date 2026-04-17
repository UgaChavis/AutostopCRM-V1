@echo off
setlocal
set "SCRIPT=%~dp0open_amnezia_dashboard.ps1"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0Open Amnezia VPN Dashboard.ps1"
if not exist "%SCRIPT%" (
  echo Launcher script not found: "%~dp0open_amnezia_dashboard.ps1"
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
