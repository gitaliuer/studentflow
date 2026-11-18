@echo off
cd /d "%~dp0"
echo Open http://127.0.0.1:8000 in your browser.
py -m uvicorn app:app --reload
pause
