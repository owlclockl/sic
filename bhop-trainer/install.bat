@echo off
setlocal
title Deadlock Bhop Trainer Installer
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "INSTALL_RC=%ERRORLEVEL%"
if not "%INSTALL_RC%"=="0" echo Installer failed with exit code %INSTALL_RC%.
echo.
pause
exit /b %INSTALL_RC%
