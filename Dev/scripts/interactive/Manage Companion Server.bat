@echo off
setlocal
title Greenroom Companion - Server Manager
set "INTERACTIVE_DIR=%~dp0"
for %%I in ("%INTERACTIVE_DIR%..\..") do cd /d "%%~fI"
echo ===================================================
echo          GREENROOM COMPANION SERVER MANAGER
echo ===================================================
echo.
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=status"
if "%ACTION%"=="--no-pause" set "ACTION=status"

call scripts\run_greenroom.bat companion %ACTION%
set "EXIT_CODE=%ERRORLEVEL%"

echo Usage: "Manage Companion Server.bat" [status^|start^|stop^|restart^|open^|enable-autostart^|disable-autostart^|enable-https^|disable-https]
echo ===================================================

if "%~1"=="--no-pause" goto :exit
if "%~2"=="--no-pause" goto :exit
if not "%HOUSEKEEPING_RUN%"=="" goto :exit
pause

:exit
exit /b %EXIT_CODE%
