@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 看山本地网关 (kanshan-frontend)  http://localhost:8686

echo ================================================
echo   看山 · 本地网关   http://localhost:8686/
echo ================================================
echo.

REM ---- 按优先级探测可用的 Python，找到第一个就用 ----
set "PY="

REM 1) Box-Agent 受管运行时（本机最稳）
if exist "%USERPROFILE%\.box-agent-kanshan\box-agent-runtime\runtime\python\python.exe" (
  set "PY=%USERPROFILE%\.box-agent-kanshan\box-agent-runtime\runtime\python\python.exe"
)

REM 2) 旧 WorkBuddy 运行时（兼容历史环境）
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\python.exe" (
  set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\python.exe"
)

REM 3) PATH 里的 py 启动器
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)

REM 4) PATH 里的 python
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)

if not defined PY (
  echo [X] 没有找到可用的 Python。
  echo.
  echo     请到 https://www.python.org/downloads/ 安装 Python，
  echo     安装时务必勾选 "Add Python to PATH"，装完重开本窗口再双击本文件。
  echo.
  echo     server.py 只用 Python 标准库，无需再装任何第三方包。
  echo.
  pause
  exit /b 1
)

echo [OK] 使用 Python: %PY%
echo [OK] 启动后浏览器会自动打开，或手动访问 http://localhost:8686/
echo [提示] 保持本黑窗口开着 = 网关在运行；关掉窗口 = 网关停止。
echo.

"%PY%" server.py

echo.
echo 网关已停止。若上方有报错，请把红字截图发出来排查。
pause
endlocal
