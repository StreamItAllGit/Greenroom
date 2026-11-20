@echo off
setlocal
REM Refresh configured editorial review queue from completed catalog titles.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run process_review_queue %*
exit /b %ERRORLEVEL%
