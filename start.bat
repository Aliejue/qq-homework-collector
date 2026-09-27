@echo off
chcp 65001 >nul
title Homework Collector
cd /d "%~dp0"

rem ============================================================
rem  Text-mode version.
rem  Keep this file ASCII-only (see start_gui.bat for why).
rem ============================================================

set "PY="

for %%P in (
  "%SystemRoot%\py.exe"
  "python"
  "python3"
  "C:\msys64\ucrt64\bin\python.exe"
  "C:\msys64\mingw64\bin\python.exe"
  "C:\msys64\clang64\bin\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
  "C:\Python314\python.exe"
  "C:\Python313\python.exe"
  "C:\Python312\python.exe"
  "C:\Python311\python.exe"
  "C:\Python310\python.exe"
  "%ProgramFiles%\Python313\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%ProgramFiles%\Python311\python.exe"
) do (
  if not defined PY (
    "%%~P" -c "import sys" >nul 2>nul && set "PY=%%~P"
  )
)

if not defined PY (
  echo.
  echo  [ERROR] Python not found.
  echo  Please install Python 3.9+ from https://www.python.org/downloads/
  echo  During setup, tick "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

"%PY%" run.py
pause
