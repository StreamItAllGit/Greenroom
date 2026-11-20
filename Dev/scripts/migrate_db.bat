@echo off
setlocal
REM Apply pending SQLite schema migrations with automated pre-migration backup.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" migrate %*
exit /b %ERRORLEVEL%
