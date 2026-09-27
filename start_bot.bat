@echo off
chcp 65001 >nul
title Homework Collector - QQ Bot
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (where py >nul 2>nul && set "PY=py")
if not defined PY (
  echo.
  echo  [ERROR] Python not found.
  echo  Please install Python 3.8+ from https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo Make sure NapCat is running and its HTTP reporting URL is:
echo   http://127.0.0.1:8765/onebot
echo.
%PY% qq_bot.py
pause
