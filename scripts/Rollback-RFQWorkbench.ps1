[CmdletBinding()]
param(
    [string]$ConfigRoot = "",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

function Set-RFQRollbackSettings {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)]$Contract
    )
    $updated = [ordered]@{}
    foreach ($property in $Settings.PSObject.Properties) { $updated[$property.Name] = $property.Value }
    $updated.release_version = [string]$Contract.Version
    $updated.source_commit = if (Test-RFQHasProperty -Object $Contract.Manifest -Name "source_commit") { [string]$Contract.Manifest.source_commit } else { "manifest:$(([string]$Contract.ManifestSha256).Substring(0, 12))" }
    $updated.updated_at = (Get-Date).ToUniversalTime().ToString("o")
    Write-RFQJson -Path (Join-Path ([string]$Settings.config_root) "settings.json") -Value $updated
}

$defaults = Get-RFQDefaultPaths
if (-not $ConfigRoot) { $ConfigRoot = $defaults.ConfigRoot }
$ConfigRoot = Assert-RFQCurrentUserRoot -Path $ConfigRoot -ParameterName "ConfigRoot"
$settings = Get-RFQSettings -ConfigRoot $ConfigRoot
$current = Get-RFQActiveRelease -Settings $settings
$previousPointer = Join-Path $ConfigRoot "previous_release.txt"
if (-not (Test-Path -LiteralPath $previousPointer -PathType Leaf)) { throw "No previous release is available." }
$previous = Assert-RFQLocalAbsolutePath -Path ((Get-Content -LiteralPath $previousPointer -Raw -Encoding UTF8).Trim()) -ParameterName "PreviousRelease"
$releasesRoot = Join-Path ([string]$settings.install_root) "releases"
[void](Assert-RFQPathContained -Path $previous -AllowedRoot $releasesRoot -ParameterName "PreviousRelease")
$contract = Get-RFQReleaseContract -PackageRoot $previous

& (Join-Path $PSScriptRoot "Stop-RFQWorkbench.ps1") -ConfigRoot $ConfigRoot | Out-Null
$activePointer = Join-Path $ConfigRoot "active_release.txt"
try {
    Write-RFQPointer -Path $activePointer -Value $previous
    Write-RFQPointer -Path $previousPointer -Value $current
    Set-RFQRollbackSettings -Settings $settings -Contract $contract
    if (-not $NoStart) {
        & (Join-Path ([string]$settings.install_root) "ops\Start-RFQWorkbench.ps1") -ConfigRoot $ConfigRoot | Out-Null
    }
}
catch {
    Write-RFQPointer -Path $activePointer -Value $current
    throw
}

[pscustomobject]@{
    Status = "rolled_back"
    Version = [string]$contract.Version
    ActiveRelease = $previous
    PreviousRelease = $current
    Started = -not [bool]$NoStart
}
