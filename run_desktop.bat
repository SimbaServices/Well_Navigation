@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m wellnav.desktop
) else (
  python -m wellnav.desktop
)
