@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 看山 · 创作校准引擎（演示启动器）

echo.
echo  ==================================================
echo    看山 · 创作校准引擎   一键演示启动
echo  ==================================================
echo.

REM ================= 1) 先看服务是不是已经在跑 =================
REM 已在跑就直接开浏览器：避免重复启动连到旧实例，也避免端口冲突
netstat -ano 2>nul | findstr /C:"127.0.0.1:8686" | findstr /I "LISTENING" >nul 2>&1
if %errorlevel%==0 (
  echo  [i] 检测到网关已在运行，直接打开演示页面。
  echo.
  start "" "http://localhost:8686/"
  echo  演示地址：http://localhost:8686/
  echo.
  echo  提示：如果想重启网关，先关掉之前那个黑窗口，再运行本文件。
  echo.
  pause
  exit /b 0
)

REM ================= 2) 找可用的 Python =================
set "PY="
if exist "%USERPROFILE%\.box-agent-kanshan\box-agent-runtime\runtime\python\python.exe" (
  set "PY=%USERPROFILE%\.box-agent-kanshan\box-agent-runtime\runtime\python\python.exe"
)
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\default\python.exe" (
  set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\python.exe"
)
if not defined PY ( where py >nul 2>nul && set "PY=py" )
if not defined PY ( where python >nul 2>nul && set "PY=python" )

if not defined PY (
  echo  [X] 没找到 Python，网关无法启动。
  echo.
  echo      去 https://www.python.org/downloads/ 安装，
  echo      安装时勾选 "Add Python to PATH"，装完重开本文件。
  echo.
  echo      本项目只用 Python 标准库，不需要再装任何第三方包。
  echo.
  pause
  exit /b 1
)
echo  [1/3] Python 就绪

REM ================= 3) 密钥预检（提前暴露问题，别等页面报错） =================
set "SECRET_OK="
if exist "secret.txt" (
  for %%A in ("secret.txt") do if %%~zA GTR 10 set "SECRET_OK=1"
)
if not defined SECRET_OK if defined ZHIHU_ACCESS_SECRET set "SECRET_OK=1"

if defined SECRET_OK (
  echo  [2/3] Access Secret 已配置，可读取真实数据
) else (
  echo  [2/3] [!] 未检测到 Access Secret
  echo        真实数据会读不出来，但 "先看演示数据" 仍可正常演示。
  echo        要用真实数据：把密钥贴进本目录的 secret.txt 再重开本文件。
  echo        ^(密钥只留本机，不进前端、不入库^)
)

echo  [3/3] 正在启动网关，浏览器会自动打开...
echo.
echo  --------------------------------------------------
echo   演示地址   http://localhost:8686/
echo   保持本窗口开着 = 服务运行中
echo   关掉本窗口     = 停止服务
echo  --------------------------------------------------
echo.

REM 网关输出含 UTF-8 符号（如 ?），必须切 UTF-8 代码页，否则 GBK 下会 UnicodeEncodeError 崩溃
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
"%PY%" server.py

echo.
echo  网关已停止。若上面有报错，把红字截图发出来即可排查。
pause
endlocal
