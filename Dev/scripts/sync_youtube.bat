@echo off
setlocal
REM Fetch live subscriber, video, and view counts across managed YouTube channels.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run sync_youtube %*
exit /b %ERRORLEVEL%
