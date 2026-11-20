@echo off
setlocal
REM Ingest known-good No-Intro and Redump .dat / .xml hash catalogs from drops\dats\ and data\dats\.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run import_dats %*
exit /b %ERRORLEVEL%
