@echo off
setlocal
REM Ingest Google Drive Takeout exports (YouTube watch history, Goodreads, InfiniteBacklog, Bluesky).
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run collect_takeout %*
exit /b %ERRORLEVEL%
