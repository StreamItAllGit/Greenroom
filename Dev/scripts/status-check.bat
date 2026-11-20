@echo off
setlocal
call "%~dp0run_greenroom.bat" status %*
exit /b %ERRORLEVEL%
