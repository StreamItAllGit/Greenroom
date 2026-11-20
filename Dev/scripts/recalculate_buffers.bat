@echo off
setlocal
REM Recompute 4-video channel release buffers dynamically from active production_cuts.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run recalculate_buffers %*
exit /b %ERRORLEVEL%
