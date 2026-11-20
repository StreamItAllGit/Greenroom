@echo off
setlocal
REM Synchronize newly added movies, shows, and anime from local Plex Media Server (:32400).
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run collect_plex %*
exit /b %ERRORLEVEL%
