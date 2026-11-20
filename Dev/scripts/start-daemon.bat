@echo off
setlocal
set "SUPERVISED=0"
echo "%*" | findstr /I /C:"--managed-by-mother" /C:"--managed-by-suite" >nul && set "SUPERVISED=1"
if "%SUPERVISED%"=="1" (
    call "%~dp0run_greenroom.bat" serve --no-tray --tray-only %*
    exit /b %ERRORLEVEL%
)
call "%~dp0run_greenroom.bat" start %*
exit /b %ERRORLEVEL%
