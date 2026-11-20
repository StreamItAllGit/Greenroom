@echo off
setlocal
REM Greenroom Universal CLI Runner
REM Natively unattended for external schedulers, Windows Task Scheduler, and CLI pipelines.
REM Optional flags: --unattended | --no-pause | --pause | --interactive

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "PY_EXE=%CD%\.venv\Scripts\python.exe"
if not exist "%PY_EXE%" (
    if exist "%CD%\app\.venv\Scripts\python.exe" (
        set "PY_EXE=%CD%\app\.venv\Scripts\python.exe"
    )
)
if not exist "%PY_EXE%" (
    echo [ERROR] Python virtual environment not found at "%CD%\.venv\Scripts\python.exe" or "%CD%\app\.venv\Scripts\python.exe"
    exit /b 1
)

if exist "%CD%\app\greenroom\__init__.py" (
    if "%PYTHONPATH%"=="" (
        set "PYTHONPATH=%CD%\app"
    ) else (
        set "PYTHONPATH=%CD%\app;%PYTHONPATH%"
    )
)

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

"%PY_EXE%" -u -m greenroom.cli %*
set "EXIT_CODE=%ERRORLEVEL%"

set "SHOULD_PAUSE=0"
set "FORCE_UNATTENDED=0"
:parse_flags
if "%~1"=="" goto :done_flags
if /I "%~1"=="--pause" set "SHOULD_PAUSE=1"
if /I "%~1"=="--interactive" set "SHOULD_PAUSE=1"
if /I "%~1"=="--unattended" set "FORCE_UNATTENDED=1"
if /I "%~1"=="--no-pause" set "FORCE_UNATTENDED=1"
shift /1
goto :parse_flags

:done_flags
if not "%HOUSEKEEPING_RUN%"=="" set "FORCE_UNATTENDED=1"
if "%FORCE_UNATTENDED%"=="1" set "SHOULD_PAUSE=0"
if "%SHOULD_PAUSE%"=="1" pause
exit /b %EXIT_CODE%
