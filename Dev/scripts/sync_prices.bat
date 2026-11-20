@echo off
setlocal
REM Synchronize the 124,000+ title PriceCharting master reference price guide into SQLite.
REM Unattended by default. Optional flags: --force | --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" sync-prices %*
exit /b %ERRORLEVEL%
