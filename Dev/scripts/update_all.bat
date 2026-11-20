@echo off
setlocal
REM Force-run every collector, Takeout importer, external API telemetry sync, and processor end-to-end.
REM Unattended by default. Optional flags: --prices | --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" update-all %*
exit /b %ERRORLEVEL%
