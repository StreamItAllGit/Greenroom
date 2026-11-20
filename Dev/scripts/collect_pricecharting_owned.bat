@echo off
setlocal
REM Import personal owned PriceCharting collection CSV from drops\manual_csv\pricecharting_collection.csv.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run collect_pricecharting_owned %*
exit /b %ERRORLEVEL%
