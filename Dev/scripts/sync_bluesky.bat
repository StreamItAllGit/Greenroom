@echo off
setlocal
REM Collect recent public posts from Bluesky AT Protocol feed.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run sync_social %*
exit /b %ERRORLEVEL%
