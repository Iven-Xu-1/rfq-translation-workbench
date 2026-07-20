[CmdletBinding()]
param([string]$ConfigRoot = "")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

$defaults = Get-RFQDefaultPaths
if (-not $ConfigRoot) { $ConfigRoot = $defaults.ConfigRoot }
$ConfigRoot = Assert-RFQCurrentUserRoot -Path $ConfigRoot -ParameterName "ConfigRoot"
$settings = Get-RFQSettings -ConfigRoot $ConfigRoot
[void](Assert-RFQLoopbackSettings -Settings $settings)
[void](Assert-RFQProviderSettings -Settings $settings)
$release = Get-RFQActiveRelease -Settings $settings
$runtimePython = Join-Path $release "runtime\python.exe"
$appRoot = Join-Path $release "app"
[void](Assert-RFQPathContained -Path $runtimePython -AllowedRoot $release -ParameterName "RuntimePython")
[void](Assert-RFQPathContained -Path $appRoot -AllowedRoot $release -ParameterName "AppRoot")
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) { throw "The embedded Python runtime is missing." }
if (-not (Test-Path -LiteralPath (Join-Path $appRoot "rfq_app\main.py") -PathType Leaf)) { throw "The application entry point is missing." }

$logRoot = Join-Path ([string]$settings.data_root) "Logs"
[void](Assert-RFQPathContained -Path $logRoot -AllowedRoot ([string]$settings.data_root) -ParameterName "LogRoot")
[System.IO.Directory]::CreateDirectory($logRoot) | Out-Null
$watchdogPid = Get-RFQStatePath -Settings $settings -Name "watchdog.pid"
$servicePid = Get-RFQStatePath -Settings $settings -Name "service.pid"
$stopFlag = Get-RFQStatePath -Settings $settings -Name "service.stop"
$PID | Set-Content -LiteralPath $watchdogPid -Encoding ASCII

try {
    while (-not (Test-Path -LiteralPath $stopFlag)) {
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $stdout = Join-Path $logRoot "service_stdout_$stamp.log"
        $stderr = Join-Path $logRoot "service_stderr_$stamp.log"
        Set-RFQProcessEnvironment -Settings $settings -ReleaseRoot $release
        $keyPath = Get-RFQKeyPath -Settings $settings
        $plainKey = Unprotect-RFQSecret -Source $keyPath
        try {
            [Environment]::SetEnvironmentVariable("VECTOR_ENGINE_API_KEY", $plainKey, "Process")
            $arguments = @(
                "-B", "-m", "uvicorn", "rfq_app.main:app",
                "--host", "127.0.0.1",
                "--port", [string][int]$settings.port,
                "--workers", "1"
            )
            $process = Start-Process -FilePath $runtimePython -ArgumentList $arguments -WorkingDirectory $appRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        }
        finally {
            [Environment]::SetEnvironmentVariable("VECTOR_ENGINE_API_KEY", $null, "Process")
            $plainKey = $null
        }
        $process.Id | Set-Content -LiteralPath $servicePid -Encoding ASCII
        $process.WaitForExit()
        if (-not (Test-Path -LiteralPath $stopFlag)) { Start-Sleep -Seconds 5 }
    }
}
finally {
    [Environment]::SetEnvironmentVariable("VECTOR_ENGINE_API_KEY", $null, "Process")
    Remove-Item -LiteralPath $servicePid -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $watchdogPid -Force -ErrorAction SilentlyContinue
}
