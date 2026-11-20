@echo off
setlocal
REM Synchronize all external APIs & platform telemetry (Buffer, Steam, RetroAchievements, Bluesky, Twitch, YouTube).
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run sync_telemetry %*
exit /b %ERRORLEVEL%
