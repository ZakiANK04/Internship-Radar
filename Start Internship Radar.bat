@echo off
rem Opens Internship Radar in your browser. Keep this window open while you use it.
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  echo Python environment not found. Run:  python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
"venv\Scripts\python.exe" radar.py
pause
