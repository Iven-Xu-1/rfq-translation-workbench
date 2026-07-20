@echo off
setlocal
set "SCRIPT=%~dp0bootstrap\Install-RFQTranslationWorkbench.ps1"
if not exist "%SCRIPT%" (
  echo Installer script was not found: %SCRIPT%
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -SourceRoot "%~dp0" -AcceptThirdPartyDownloads
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo Installation failed with exit code %RC%.
exit /b %RC%
