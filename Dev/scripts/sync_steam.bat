@echo off
setlocal
REM Refresh owned Steam PC games and playtime telemetry.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run sync_steam %*
exit /b %ERRORLEVEL%
