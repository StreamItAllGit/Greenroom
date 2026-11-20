@echo off
setlocal
REM Run the idempotent 6-stage database deduplication and healing engine.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run heal_db %*
exit /b %ERRORLEVEL%
