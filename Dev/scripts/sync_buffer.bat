@echo off
setlocal
REM Ingest sent and queued social posts across connected Buffer channels.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run sync_buffer %*
exit /b %ERRORLEVEL%
