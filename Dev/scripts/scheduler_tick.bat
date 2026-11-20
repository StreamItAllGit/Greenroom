@echo off
setlocal
REM Automated Scheduler Target: Evaluate config\schedule.yaml and execute all due enabled jobs.
REM Unattended by default. Optional flags: --force | --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" tick %*
exit /b %ERRORLEVEL%
