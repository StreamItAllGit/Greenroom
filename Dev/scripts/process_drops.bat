@echo off
setlocal
REM Scan drops\agent_queue\ (and Google Drive Greenroom_Drops), ingest Web AI Agent drop blobs, and sort into drops\processed\.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run process_drops %*
exit /b %ERRORLEVEL%
