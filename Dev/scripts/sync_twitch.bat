@echo off
setlocal
REM Archive recent Twitch past broadcast VODs into video_titles.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run sync_twitch %*
exit /b %ERRORLEVEL%
