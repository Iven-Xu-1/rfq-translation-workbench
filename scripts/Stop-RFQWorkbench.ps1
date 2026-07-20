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
$stopFlag = Get-RFQStatePath -Settings $settings -Name "service.stop"
"stop" | Set-Content -LiteralPath $stopFlag -Encoding ASCII

$watchdogPid = Get-RFQStatePath -Settings $settings -Name "watchdog.pid"
$servicePid = Get-RFQStatePath -Settings $settings -Name "service.pid"
$watchdog = Get-RFQManagedProcess -PidPath $watchdogPid
$service = Get-RFQManagedProcess -PidPath $servicePid

if ($watchdog) {
    if (-not (Test-RFQProcessOwned -Process $watchdog -Settings $settings -Kind "watchdog")) {
        throw "The watchdog PID file does not belong to this installation."
    }
    Stop-RFQProcessTree -RootProcessId ([int]$watchdog.ProcessId)
}
elseif ($service) {
    if (-not (Test-RFQProcessOwned -Process $service -Settings $settings -Kind "service")) {
        throw "The service PID file does not belong to this installation."
    }
    Stop-RFQProcessTree -RootProcessId ([int]$service.ProcessId)
}

Remove-Item -LiteralPath $watchdogPid -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $servicePid -Force -ErrorAction SilentlyContinue
[pscustomobject]@{ Status = "stopped"; ListenHost = "127.0.0.1"; Port = [int]$settings.port }
