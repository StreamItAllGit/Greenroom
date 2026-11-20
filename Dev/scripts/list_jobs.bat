@echo off
setlocal
REM List all registered Greenroom jobs and their schedule.yaml execution intervals.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" list-jobs %*
exit /b %ERRORLEVEL%
