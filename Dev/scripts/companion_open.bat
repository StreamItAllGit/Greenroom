@echo off
setlocal
REM Start background Companion daemon if needed and open the chromeless Windows desktop app window.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" companion open %*
exit /b %ERRORLEVEL%
