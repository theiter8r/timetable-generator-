@echo off
rem ---------------------------------------------------------------------
rem  Run the timetable app on Windows. Double-click it, or run it here.
rem
rem    start.bat                 first free port from 8000, opens a browser
rem    start.bat 8080            ...on this port instead
rem    start.bat --no-browser    don't open a browser
rem    start.bat --lan           also reachable from other machines on the network
rem
rem  Stop the app with Ctrl-C, or by closing this window. Nothing is lost:
rem  everything you enter is saved to data\config.json as you go.
rem ---------------------------------------------------------------------
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PORT=8000"
set "HOST=127.0.0.1"
set "OPENBROWSER=1"

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--no-browser" (set "OPENBROWSER=0") else (
    if /i "%~1"=="--lan" (set "HOST=0.0.0.0") else (set "PORT=%~1")
)
shift
goto parse
:parsed

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  The app is not installed yet. Run this first:
    echo      build.bat
    echo.
    pause
    exit /b 1
)

rem Port 8000 is popular; rather than dying with "address already in use",
rem walk up until something is free. usebackq keeps the quoted paths intact.
for /f "usebackq delims=" %%P in (`".venv\Scripts\python.exe" "scripts\pick_port.py" %PORT%`) do set "PORT=%%P"

set "URL=http://localhost:%PORT%"

if "%OPENBROWSER%"=="1" call :open_browser

echo.
echo  Timetable Generator  -^>  %URL%
if "%HOST%"=="0.0.0.0" echo  Reachable from other machines on this network.
echo  Press Ctrl-C to stop.
echo.

".venv\Scripts\python.exe" -m timetable.cli serve --host %HOST% --port %PORT%

rem Double-clicked windows close the instant the server stops, taking any error
rem message with them; hold the window open so it can be read.
if errorlevel 1 (
    echo.
    echo  The app stopped with an error. If it says "address already in use",
    echo  try:  start.bat 8090
    echo.
    pause
)
exit /b 0

:open_browser
rem A moment behind the server, so the first request doesn't hit a closed port.
rem Without PowerShell there is nothing to wait with, so just open it now and
rem let the browser's own reload handle a page that arrives a second early.
where powershell >nul 2>nul
if errorlevel 1 (
    start "" "%URL%"
) else (
    start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process '%URL%'"
)
exit /b 0
