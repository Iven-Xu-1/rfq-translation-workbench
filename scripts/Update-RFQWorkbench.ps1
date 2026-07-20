[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [string]$ConfigRoot = "",
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

function Set-RFQReleaseSettings {
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
$PackageRoot = Assert-RFQLocalAbsolutePath -Path $PackageRoot -ParameterName "PackageRoot" -DisallowDriveRoot
$ConfigRoot = Assert-RFQCurrentUserRoot -Path $ConfigRoot -ParameterName "ConfigRoot"
$settings = Get-RFQSettings -ConfigRoot $ConfigRoot
$contract = Get-RFQReleaseContract -PackageRoot $PackageRoot
$oldRelease = Get-RFQActiveRelease -Settings $settings
$packageInstaller = Join-Path $PackageRoot "scripts\Install-RFQWorkbench.ps1"
[void](Assert-RFQPathContained -Path $packageInstaller -AllowedRoot $PackageRoot -ParameterName "PackageInstaller")
if (-not (Test-Path -LiteralPath $packageInstaller -PathType Leaf)) {
    throw "Release package is missing scripts\Install-RFQWorkbench.ps1."
}

# Copy and verify the new release without changing the active pointer.
& $packageInstaller `
    -PackageRoot $PackageRoot `
    -InstallRoot ([string]$settings.install_root) `
    -DataRoot ([string]$settings.data_root) `
    -ConfigRoot ([string]$settings.config_root) `
    -Port ([int]$settings.port) `
    -NoShortcuts `
    -DoNotActivate | Out-Null

$newRelease = Join-Path ([string]$settings.install_root) ("releases\{0}" -f [string]$contract.Version)
[void](Assert-RFQPathContained -Path $newRelease -AllowedRoot (Join-Path ([string]$settings.install_root) "releases") -ParameterName "NewRelease")
[void](Get-RFQReleaseContract -PackageRoot $newRelease)
if ($newRelease.Equals($oldRelease, [StringComparison]::OrdinalIgnoreCase)) {
    [pscustomobject]@{ Status = "already_current"; Version = [string]$contract.Version; ActiveRelease = $oldRelease }
    exit 0
}

& (Join-Path $PSScriptRoot "Stop-RFQWorkbench.ps1") -ConfigRoot $ConfigRoot | Out-Null
$activePointer = Join-Path $ConfigRoot "active_release.txt"
$previousPointer = Join-Path $ConfigRoot "previous_release.txt"
try {
    Write-RFQPointer -Path $previousPointer -Value $oldRelease
    Write-RFQPointer -Path $activePointer -Value $newRelease
    Set-RFQReleaseSettings -Settings $settings -Contract $contract
    if (-not $NoStart) {
        & (Join-Path ([string]$settings.install_root) "ops\Start-RFQWorkbench.ps1") -ConfigRoot $ConfigRoot | Out-Null
    }
}
catch {
    Write-RFQPointer -Path $activePointer -Value $oldRelease
    $oldContract = Get-RFQReleaseContract -PackageRoot $oldRelease
    Set-RFQReleaseSettings -Settings $settings -Contract $oldContract
    if (-not $NoStart) {
        try { & (Join-Path ([string]$settings.install_root) "ops\Start-RFQWorkbench.ps1") -ConfigRoot $ConfigRoot | Out-Null }
        catch { }
    }
    throw
}

[pscustomobject]@{
    Status = "updated"
    Version = [string]$contract.Version
    ActiveRelease = $newRelease
    PreviousRelease = $oldRelease
    Started = -not [bool]$NoStart
    RollbackAvailable = $true
}
