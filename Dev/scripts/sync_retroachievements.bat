@echo off
setlocal
REM Reconcile beaten, mastered, and recently played retro titles from RetroAchievements.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run sync_retroachievements %*
exit /b %ERRORLEVEL%
