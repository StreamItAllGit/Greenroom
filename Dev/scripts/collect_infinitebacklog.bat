@echo off
setlocal
REM Import InfiniteBacklog CSV exports placed in drops\manual_csv\.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run collect_infinitebacklog %*
exit /b %ERRORLEVEL%
