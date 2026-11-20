@echo off
setlocal
call "%~dp0run_greenroom.bat" backup %*
exit /b %ERRORLEVEL%
