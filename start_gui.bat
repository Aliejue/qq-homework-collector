@echo off
rem ============================================================
rem  Homework Collector - graphical console launcher
rem
rem  IMPORTANT: keep this file ASCII-only.
rem  cmd.exe decodes .bat files with the console code page, so
rem  any UTF-8 Chinese text in here gets mis-decoded and the
rem  comment lines end up being executed as commands.
rem  All Chinese messages are shown by launch_gui.py instead.
rem
rem  This script only needs to find SOME working Python to run
rem  launch_gui.py. That script then picks a Python that really
rem  has tkinter and opens the window.
rem ============================================================

title Homework Collector
cd /d "%~dp0"

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
  echo  [ERROR] No usable Python found on this computer.
  echo.
  echo  Please install Python 3.9 or newer:
  echo      https://www.python.org/downloads/
  echo  Keep the "Add python.exe to PATH" checkbox ticked,
  echo  and keep the default "tcl/tk and IDLE" component.
  echo.
  echo  After installing, double-click this file again.
  echo  You can also use the text-mode version: start.bat
  echo.
  pause
  exit /b 1
)

"%PY%" "%~dp0launch_gui.py"

if errorlevel 1 (
  echo.
  echo  Startup failed. See the dialog box for details.
  echo.
  pause
)

exit /b 0
