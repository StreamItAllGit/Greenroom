@echo off
setlocal
REM Compute CRC32/MD5/SHA-1 checksums on local ROMs/ISOs and verify against loaded No-Intro & Redump DATs.
REM Unattended by default. Optional flags: --unattended | --no-pause | --pause
call "%~dp0run_greenroom.bat" run verify_hashes %*
exit /b %ERRORLEVEL%
