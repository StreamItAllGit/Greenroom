@echo off
setlocal
REM Safe Zero-Data-Loss Deployment from Workbench (%~dp0..) to %LOCALAPPDATA%\Programs\Greenroom (or --suite-dir / --target-dir)
REM Natively unattended. Optional flags: --standalone | --suite-dir DIR | --target-dir DIR | --dry-run | --unattended | --pause

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Python virtual environment not found at "%CD%\.venv\Scripts\python.exe"
    exit /b 1
)

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

".venv\Scripts\python.exe" scripts\deploy_to_programs.py %*
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
