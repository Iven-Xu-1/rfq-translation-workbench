[CmdletBinding()]
param([string]$ConfigRoot = "")

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

$defaults = Get-RFQDefaultPaths
if (-not $ConfigRoot) { $ConfigRoot = $defaults.ConfigRoot }
$ConfigRoot = Assert-RFQCurrentUserRoot -Path $ConfigRoot -ParameterName "ConfigRoot"
$settings = Get-RFQSettings -ConfigRoot $ConfigRoot
$release = Get-RFQActiveRelease -Settings $settings
$watchdog = Get-RFQManagedProcess -PidPath (Get-RFQStatePath -Settings $settings -Name "watchdog.pid")
$service = Get-RFQManagedProcess -PidPath (Get-RFQStatePath -Settings $settings -Name "service.pid")
$health = Invoke-RFQHealth -Settings $settings -TimeoutSeconds 5
$keyConfigured = Test-Path -LiteralPath (Get-RFQKeyPath -Settings $settings) -PathType Leaf

[pscustomobject]@{
    Status = if ($health) { [string]$health.status } else { "unreachable" }
    Url = "http://127.0.0.1:$([int]$settings.port)/"
    ListenHost = "127.0.0.1"
    Port = [int]$settings.port
    Workers = 1
    ReleaseVersion = [string]$settings.release_version
    SourceCommit = [string]$settings.source_commit
    ReleaseRoot = $release
    WatchdogRunning = [bool]$watchdog
    ServiceRunning = [bool]$service
    ApiKeyConfigured = [bool]$keyConfigured
    ApiKeyStorage = "dpapi_current_user"
    ApiKeyValueExposed = $false
    AutostartEnabled = $false
}
