@echo off
setlocal
cd /d "%~dp0"

if not exist logs mkdir logs
set SUPFILE=logs\supervisor.log

:loop
echo [%date% %time%] starting repo-guardian >> "%SUPFILE%"
pythonw.exe main.py
echo [%date% %time%] main.py exited with code %errorlevel% - restarting in 10s >> "%SUPFILE%"
timeout /t 10 /nobreak > nul
goto loop
