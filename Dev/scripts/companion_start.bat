@echo off
setlocal
REM Ensure the background Greenroom Companion web/PWA daemon (:8420) is running (safe idempotent watchdog target).
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" companion start %*
exit /b %ERRORLEVEL%
