@echo off
setlocal
REM Bypass schedule intervals and immediately force-run all enabled jobs in config\schedule.yaml.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" tick --force %*
exit /b %ERRORLEVEL%
