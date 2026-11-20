@echo off
setlocal
title Greenroom - Desktop Launcher
cd /d "%~dp0"
call scripts\run_greenroom.bat open --no-pause
set "EXIT_CODE=%ERRORLEVEL%"
exit /b %EXIT_CODE%
