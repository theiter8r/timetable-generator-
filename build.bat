@echo off
rem ---------------------------------------------------------------------
rem  Install the timetable app on Windows.
rem
rem  Everything lands in a .venv folder inside this directory -- nothing is
rem  installed system-wide, so uninstalling is deleting this folder. Safe to
rem  run again at any time; that is also how you upgrade after new code.
rem
rem    build.bat            install (or repair) the app
rem    build.bat --clean    delete .venv first and install from scratch
rem    build.bat --test     ...and run the test suite afterwards
rem ---------------------------------------------------------------------
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "CLEAN=0"
set "RUNTESTS=0"
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--clean" set "CLEAN=1"
if /i "%~1"=="--test"  set "RUNTESTS=1"
if /i "%~1"=="--tests" set "RUNTESTS=1"
shift
goto parse
:parsed

echo.
echo  Timetable Generator - install
echo  =============================
echo.

rem --- 1. find a Python we can use -------------------------------------
rem 3.11 is the floor: the code uses "X | Y" unions at runtime and OR-Tools
rem publishes no wheels for anything older.

rem Each candidate is tried in a subroutine rather than a for-loop: a python
rem -c snippet is full of the characters cmd's block parser mishandles.
set "PYTHON="
call :try_python py -3.13
if not defined PYTHON call :try_python py -3.12
if not defined PYTHON call :try_python py -3.11
if not defined PYTHON call :try_python py -3
if not defined PYTHON call :try_python python

if not defined PYTHON (
    echo  Python 3.11 or newer is required, and none was found.
    echo.
    echo  Install it from https://www.python.org/downloads/ and tick
    echo  "Add python.exe to PATH" in the installer, then run build.bat again.
    echo.
    exit /b 1
)

for /f "delims=" %%V in ('%PYTHON% --version 2^>^&1') do set "PYVER=%%V"
echo  Python:  %PYVER%

rem --- 2. clean, if asked ----------------------------------------------

if "%CLEAN%"=="1" (
    if exist ".venv" (
        echo  Removing the existing .venv
        rmdir /s /q ".venv"
    )
)

rem --- 3. install -------------------------------------------------------
rem uv, if it is here, installs the exact versions pinned in uv.lock and is a
rem good deal faster. Without it, a plain venv plus pip resolves the same
rem packages fresh; both give a working app.

echo.
where uv >nul 2>nul
if %errorlevel% equ 0 (
    echo  Installing dependencies with uv ^(locked versions^)
    uv sync
    if errorlevel 1 goto installfailed
) else (
    echo  Installing dependencies with pip into .venv
    echo  ^(install "uv" for a faster, version-locked install: https://docs.astral.sh/uv/^)
    if not exist ".venv\Scripts\python.exe" (
        %PYTHON% -m venv .venv
        if errorlevel 1 goto venvfailed
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -e .
    if errorlevel 1 goto installfailed
)

if not exist ".venv\Scripts\python.exe" goto venvfailed

rem --- 4. prove it actually works ---------------------------------------

echo.
echo  Checking the install
".venv\Scripts\python.exe" "scripts\check_install.py"
if errorlevel 1 (
    echo.
    echo  The app is installed but does not import cleanly.
    echo  Try again with:  build.bat --clean
    exit /b 1
)

if "%RUNTESTS%"=="1" (
    echo.
    echo  Running the test suite
    ".venv\Scripts\python.exe" -m pytest -q
    if errorlevel 1 (
        echo.
        echo  The tests did not pass.
        exit /b 1
    )
)

echo.
echo  Done.
echo.
echo    Start the app by double-clicking start.bat, or running it here.
echo    It opens http://localhost:8000 in your browser and walks you
echo    through setup the first time.
echo.
echo    Other commands:
echo      .venv\Scripts\python.exe -m timetable.cli solve      generate from the saved config
echo      .venv\Scripts\python.exe -m timetable.cli validate   pre-flight checks only
echo      .venv\Scripts\python.exe -m timetable.cli reset      restore the sample dataset
echo.
exit /b 0

:try_python
rem %* is the whole candidate command, e.g. "py -3.12".
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if %errorlevel% equ 0 set "PYTHON=%*"
exit /b 0

:venvfailed
echo.
echo  Could not create the virtual environment in .venv
echo  Check that you can write to this folder, then try:  build.bat --clean
exit /b 1

:installfailed
echo.
echo  The dependencies could not be installed. This step needs a working
echo  internet connection. Then try:  build.bat --clean
exit /b 1
