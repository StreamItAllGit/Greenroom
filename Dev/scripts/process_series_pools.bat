@echo off
setlocal
REM Audit and update dynamic selection weights across configured series pools.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run process_series_pools %*
exit /b %ERRORLEVEL%
