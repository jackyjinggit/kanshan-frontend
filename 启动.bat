@echo off
setlocal
cd /d "%~dp0"
title Kanshan Gateway (kanshan-frontend)

echo ================================================
echo   Kanshan local gateway  -  http://localhost:8686
echo ================================================

set "PY=C:\Users\Q1070\.workbuddy\binaries\python\envs\default\python.exe"
if not exist "%PY%" set "PY=python"

echo [OK] Python: %PY%
echo [OK] Open http://localhost:8686/ after the server starts.
echo.

"%PY%" server.py

echo.
echo Gateway stopped. Review the error above if it did not start.
pause
endlocal
