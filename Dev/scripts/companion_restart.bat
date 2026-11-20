@echo off
setlocal
REM Restart the background Greenroom Companion web/PWA daemon (:8420).
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" companion restart %*
exit /b %ERRORLEVEL%
