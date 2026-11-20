@echo off
setlocal
REM Stop the background Greenroom Companion web/PWA daemon (:8420).
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" companion stop %*
exit /b %ERRORLEVEL%
