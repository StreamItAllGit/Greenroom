@echo off
setlocal
REM Initialize SQLite database schema (data\schema.sql) in WAL mode.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" init-db %*
exit /b %ERRORLEVEL%
