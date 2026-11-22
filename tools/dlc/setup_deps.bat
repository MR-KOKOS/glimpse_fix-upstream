@echo off
rem glimpse_fix - checks (and with -Install / -Update installs) the programs the DLC build needs.
rem   setup_deps.bat            show what is there and what is missing
rem   setup_deps.bat -Install   install what is missing (current versions)
rem   setup_deps.bat -Update    install what is missing and update the rest
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_deps.ps1" %*
set RC=%ERRORLEVEL%
if "%~1"=="" pause
exit /b %RC%
