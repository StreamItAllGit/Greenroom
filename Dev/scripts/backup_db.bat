@echo off
setlocal
REM Create an immediate online WAL-safe atomic SQLite database snapshot in data\backups\.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" backup %*
exit /b %ERRORLEVEL%
