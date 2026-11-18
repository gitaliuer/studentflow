@echo off
cd /d "%~dp0"
py -m pip install -r requirements.txt
if errorlevel 1 goto failed
py set_passwords.py
if errorlevel 1 goto failed
echo Setup completed. Run start_windows.bat to open the site.
pause
exit /b 0
:failed
echo Setup failed. Check Python installation and messages above.
pause
exit /b 1
