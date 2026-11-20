@echo off
setlocal
REM Synchronize Content Management markdown (--cuts-md), Content Ledger TSV (--ledger-tsv), Completed TSV (--completed-tsv), and buffers.
REM Unattended by default. Optional flags: --cuts-md PATH | --ledger-tsv PATH | --completed-tsv PATH | --unattended | --pause
call "%~dp0run_greenroom.bat" sync-content %*
exit /b %ERRORLEVEL%
