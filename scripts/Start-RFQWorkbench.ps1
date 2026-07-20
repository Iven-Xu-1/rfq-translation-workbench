[CmdletBinding()]
param(
    [string]$ConfigRoot = "",
    [int]$HealthTimeoutSeconds = 60
)

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

$watchdogPid = Get-RFQStatePath -Settings $settings -Name "watchdog.pid"
$existing = Get-RFQManagedProcess -PidPath $watchdogPid
if ($existing) {
    if (-not (Test-RFQProcessOwned -Process $existing -Settings $settings -Kind "watchdog")) {
        throw "The watchdog PID file does not belong to this installation."
    }
    $health = Invoke-RFQHealth -Settings $settings -TimeoutSeconds 5
    if ($health) {
        [pscustomobject]@{ Status = "already_running"; Url = "http://127.0.0.1:$([int]$settings.port)/"; Version = [string]$settings.release_version }
        exit 0
    }
}

$stopFlag = Get-RFQStatePath -Settings $settings -Name "service.stop"
Remove-Item -LiteralPath $stopFlag -Force -ErrorAction SilentlyContinue
$watchScript = Join-Path ([string]$settings.install_root) "ops\Watch-RFQWorkbench.ps1"
[void](Assert-RFQPathContained -Path $watchScript -AllowedRoot ([string]$settings.install_root) -ParameterName "WatchScript")
if (-not (Test-Path -LiteralPath $watchScript -PathType Leaf)) { throw "The installed watchdog script is missing." }

$powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $powerShell -PathType Leaf)) { throw "Windows PowerShell is not available." }
$arguments = @(
    "-NoProfile",
    "-File",
    ('"{0}"' -f $watchScript),
    "-ConfigRoot",
    ('"{0}"' -f $ConfigRoot)
)
[void](Start-Process -FilePath $powerShell -ArgumentList $arguments -WorkingDirectory (Split-Path -Parent $watchScript) -WindowStyle Hidden -PassThru)

$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
do {
    Start-Sleep -Milliseconds 500
    $health = Invoke-RFQHealth -Settings $settings -TimeoutSeconds 3
    if ($health -and [string]$health.status -in @("healthy", "degraded")) {
        [pscustomobject]@{
            Status = "running"
            Health = [string]$health.status
            Url = "http://127.0.0.1:$([int]$settings.port)/"
            Version = [string]$settings.release_version
            ReleaseRoot = $release
            Workers = 1
        }
        exit 0
    }
} while ((Get-Date) -lt $deadline)

throw "The service did not become healthy before the timeout. Review local logs under the configured data root."
