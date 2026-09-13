@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Kanshan Demo Launcher - http://localhost:8686

echo.
echo  ==================================================
echo    KANSHAN  Creator Calibration Engine
echo    One-click demo launcher
echo  ==================================================
echo.

REM ---- 1) already running? just open browser ----
netstat -ano 2>nul | findstr /C:"127.0.0.1:8686" | findstr /I "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo  [i] Gateway already running. Opening demo page...
  echo.
  start "" "http://localhost:8686/"
  echo      http://localhost:8686/
  echo.
  echo  [Tip] To restart: close the old black window, then run this file again.
  echo.
  pause
  exit /b 0
)

REM ---- 2) find python ----
set "PY="
if exist "%USERPROFILE%\.box-agent-kanshanox-agent-runtimeuntime\python\python.exe" set "PY=%USERPROFILE%\.box-agent-kanshanox-agent-runtimeuntime\python\python.exe"
if not defined PY if exist "%USERPROFILE%\.workbuddyinaries\python\envs\default\python.exe" set "PY=%USERPROFILE%\.workbuddyinaries\python\envs\default\python.exe"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"

if not defined PY (
  echo  [X] Python not found.
  echo.
  echo      Install from https://www.python.org/downloads/
  echo      IMPORTANT: check "Add Python to PATH" during setup.
  echo      Only stdlib is used - no extra packages needed.
  echo.
  pause
  exit /b 1
)
echo  [1/3] Python  OK

REM ---- 3) secret precheck ----
set "SECRET_OK="
if exist "secret.txt" for %%A in ("secret.txt") do if %%~zA GTR 10 set "SECRET_OK=1"
if not defined SECRET_OK if defined ZHIHU_ACCESS_SECRET set "SECRET_OK=1"

if defined SECRET_OK (
  echo  [2/3] Access Secret  OK  - real data enabled
) else (
  echo  [2/3] Access Secret  MISSING
  echo        Real data will not load. Demo data still works.
  echo        To fix: paste your key into secret.txt in this folder.
)

echo  [3/3] Starting gateway... browser will open automatically.
echo.
echo  --------------------------------------------------
echo    Demo URL :  http://localhost:8686/
echo    Keep this window OPEN  = service running
echo    Close this window      = service stopped
echo  --------------------------------------------------
echo.

chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
"%PY%" server.py

echo.
echo  Gateway stopped. If errors above, screenshot them for support.
pause
endlocal
