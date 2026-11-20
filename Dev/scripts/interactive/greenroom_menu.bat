@echo off
setlocal
REM Greenroom Interactive Quick-Launcher Menu (scripts\interactive\greenroom_menu.bat)
REM Safe guard: if --unattended / --no-pause or HOUSEKEEPING_RUN is set, prints status and exits immediately.

set "INTERACTIVE_DIR=%~dp0"
for %%I in ("%INTERACTIVE_DIR%..") do set "SCRIPT_DIR=%%~fI\"
set "FORCE_UNATTENDED=0"
if not "%HOUSEKEEPING_RUN%"=="" set "FORCE_UNATTENDED=1"
:check_args
if "%~1"=="" goto :after_args
if /I "%~1"=="--unattended" set "FORCE_UNATTENDED=1"
if /I "%~1"=="--no-pause" set "FORCE_UNATTENDED=1"
shift /1
goto :check_args

:after_args
if "%FORCE_UNATTENDED%"=="1" goto :run_unattended

title Greenroom - Interactive Task Launcher
:menu
cls
echo ======================================================================
echo                   GREENROOM ECOSYSTEM TASK LAUNCHER
echo ======================================================================
echo  [Orchestration ^& Scheduler]
echo    1. Run Scheduler Tick (Due Jobs Only)      [scheduler_tick.bat]
echo    2. Force Scheduler Tick (All Enabled Jobs) [scheduler_tick_force.bat]
echo    3. Force Update All (Full Ecosystem Sync)  [update_all.bat]
echo    4. Ecosystem Status ^& Recent Runs          [status.bat]
echo    5. List Registered Jobs ^& Intervals        [list_jobs.bat]
echo.
echo  [Catalog, Media ^& Hash Verification]
echo    6. Scan Local ROM ^& Disc Rip Library       [collect_library.bat]
echo    7. Sync Local Plex Media Server            [collect_plex.bat]
echo    8. Ingest Google Drive Takeout             [collect_takeout.bat]
echo    9. Import InfiniteBacklog CSV Drops        [collect_infinitebacklog.bat]
echo   10. Import My PriceCharting Owned CSV       [collect_pricecharting_owned.bat]
echo   11. Import No-Intro / Redump DATs           [import_dats.bat]
echo   12. Verify ROM ^& ISO Hashes Against DATs    [verify_hashes.bat]
echo   13. Sync 124k PriceCharting Reference Guide [sync_prices.bat]
echo.
echo  [Production, Agents ^& External Telemetry]
echo   14. Process Web AI Agent Drops Queue        [process_drops.bat]
echo   15. Recalculate Channel Release Buffers     [recalculate_buffers.bat]
echo   16. Refresh Editorial Review Queue          [process_review_queue.bat]
echo   17. Audit Dynamic Series Pools              [process_series_pools.bat]
echo   18. Sync All External Platform Telemetry    [sync_telemetry.bat]
echo.
echo  [Database ^& Companion Server]
echo   19. Create Atomic SQLite Database Backup    [backup_db.bat]
echo   20. Run Pending Database Migrations         [migrate_db.bat]
echo   21. Companion Server Status ^& Phone URLs    [companion_status.bat]
echo   22. Open Greenroom Companion Desktop Window [companion_open.bat]
echo   23. Restart Companion Background Daemon     [companion_restart.bat]
echo.
echo    Q. Quit
echo ======================================================================
set "CHOICE="
set /p "CHOICE=Select an option (1-23 or Q): "

if /I "%CHOICE%"=="Q" exit /b 0
if "%CHOICE%"=="1"  call "%SCRIPT_DIR%scheduler_tick.bat"
if "%CHOICE%"=="2"  call "%SCRIPT_DIR%scheduler_tick_force.bat"
if "%CHOICE%"=="3"  call "%SCRIPT_DIR%update_all.bat"
if "%CHOICE%"=="4"  call "%SCRIPT_DIR%status.bat"
if "%CHOICE%"=="5"  call "%SCRIPT_DIR%list_jobs.bat"
if "%CHOICE%"=="6"  call "%SCRIPT_DIR%collect_library.bat"
if "%CHOICE%"=="7"  call "%SCRIPT_DIR%collect_plex.bat"
if "%CHOICE%"=="8"  call "%SCRIPT_DIR%collect_takeout.bat"
if "%CHOICE%"=="9"  call "%SCRIPT_DIR%collect_infinitebacklog.bat"
if "%CHOICE%"=="10" call "%SCRIPT_DIR%collect_pricecharting_owned.bat"
if "%CHOICE%"=="11" call "%SCRIPT_DIR%import_dats.bat"
if "%CHOICE%"=="12" call "%SCRIPT_DIR%verify_hashes.bat"
if "%CHOICE%"=="13" call "%SCRIPT_DIR%sync_prices.bat"
if "%CHOICE%"=="14" call "%SCRIPT_DIR%process_drops.bat"
if "%CHOICE%"=="15" call "%SCRIPT_DIR%recalculate_buffers.bat"
if "%CHOICE%"=="16" call "%SCRIPT_DIR%process_review_queue.bat"
if "%CHOICE%"=="17" call "%SCRIPT_DIR%process_series_pools.bat"
if "%CHOICE%"=="18" call "%SCRIPT_DIR%sync_telemetry.bat"
if "%CHOICE%"=="19" call "%SCRIPT_DIR%backup_db.bat"
if "%CHOICE%"=="20" call "%SCRIPT_DIR%migrate_db.bat"
if "%CHOICE%"=="21" call "%SCRIPT_DIR%companion_status.bat"
if "%CHOICE%"=="22" call "%SCRIPT_DIR%companion_open.bat"
if "%CHOICE%"=="23" call "%SCRIPT_DIR%companion_restart.bat"

echo.
pause
goto :menu

:run_unattended
call "%SCRIPT_DIR%run_greenroom.bat" status --no-pause
exit /b %ERRORLEVEL%
