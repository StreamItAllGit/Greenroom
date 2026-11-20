@echo off
setlocal
call "%~dp0run_greenroom.bat" stop %*
exit /b %ERRORLEVEL%
