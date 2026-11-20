@echo off
setlocal
REM Print Greenroom Companion daemon health, PID, Windows App URL, and Android/Tailscale HTTPS PWA URL (:8443).
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" companion status %*
exit /b %ERRORLEVEL%
