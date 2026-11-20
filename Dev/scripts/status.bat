@echo off
setlocal
REM Print database record counts, channel release buffers, external platform telemetry, and recent system runs.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" status %*
exit /b %ERRORLEVEL%
