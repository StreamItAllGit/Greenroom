@echo off
setlocal
REM Scan local ROM sets and personal disc rips into the canonical games catalog.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run collect_library %*
exit /b %ERRORLEVEL%
