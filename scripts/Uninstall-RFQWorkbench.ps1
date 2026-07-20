[CmdletBinding()]
param(
    [string]$ConfigRoot = "",
    [string]$ShortcutRoot = "",
    [switch]$RemoveLocalData,
    [string]$ExpectedDataRoot = "",
    [string]$DataDeletionConfirmation = "",
    [switch]$RemoveConfiguration,
    [string]$ConfigurationDeletionConfirmation = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

$defaults = Get-RFQDefaultPaths
if (-not $ConfigRoot) { $ConfigRoot = $defaults.ConfigRoot }
if (-not $ShortcutRoot) { $ShortcutRoot = $defaults.ShortcutRoot }

# Deletion authorization and string-only local-path gates precede settings/filesystem access.
if ($RemoveLocalData) {
    if ($DataDeletionConfirmation -cne "DELETE_RFQ_LOCAL_DATA") {
        throw "Removing project data requires the exact phrase DELETE_RFQ_LOCAL_DATA."
    }
    if ([string]::IsNullOrWhiteSpace($ExpectedDataRoot)) {
        throw "ExpectedDataRoot is required when removing project data."
    }
    $ExpectedDataRoot = Assert-RFQCurrentUserRoot -Path $ExpectedDataRoot -ParameterName "ExpectedDataRoot"
}
if ($RemoveConfiguration -and $ConfigurationDeletionConfirmation -cne "DELETE_RFQ_LOCAL_CONFIGURATION") {
    throw "Removing configuration and the DPAPI Key requires the exact phrase DELETE_RFQ_LOCAL_CONFIGURATION."
}

$ConfigRoot = Assert-RFQCurrentUserRoot -Path $ConfigRoot -ParameterName "ConfigRoot"
$ShortcutRoot = Assert-RFQShortcutRoot -Path $ShortcutRoot
$settings = Get-RFQSettings -ConfigRoot $ConfigRoot
$installRoot = Assert-RFQCurrentUserRoot -Path ([string]$settings.install_root) -ParameterName "settings.install_root"
$dataRoot = Assert-RFQCurrentUserRoot -Path ([string]$settings.data_root) -ParameterName "settings.data_root"
if ($RemoveLocalData -and -not $dataRoot.Equals($ExpectedDataRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "ExpectedDataRoot does not exactly match the configured data root."
}
if ((Test-RFQPathOverlap -First $installRoot -Second $dataRoot) -or
    (Test-RFQPathOverlap -First $installRoot -Second $ConfigRoot) -or
    (Test-RFQPathOverlap -First $dataRoot -Second $ConfigRoot)) {
    throw "Install, configuration, and data roots overlap; refusing uninstall."
}

& (Join-Path $PSScriptRoot "Stop-RFQWorkbench.ps1") -ConfigRoot $ConfigRoot | Out-Null

$shortcutNames = @(
    "Start RFQ Translation Workbench.lnk",
    "Stop RFQ Translation Workbench.lnk",
    "RFQ Translation Workbench Status.lnk",
    "Open RFQ Translation Workbench.url"
)
foreach ($name in $shortcutNames) {
    $path = Join-Path $ShortcutRoot $name
    [void](Assert-RFQPathContained -Path $path -AllowedRoot $ShortcutRoot -ParameterName "ShortcutDeletion")
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $ShortcutRoot -PathType Container) {
    if (@(Get-ChildItem -LiteralPath $ShortcutRoot -Force).Count -eq 0) { Remove-Item -LiteralPath $ShortcutRoot -Force }
}

[void](Assert-RFQPathContained -Path $installRoot -AllowedRoot $defaults.LocalAppData -ParameterName "InstallRootDeletion")
Remove-RFQLongPathTree -Path $installRoot

if ($RemoveLocalData) {
    [void](Assert-RFQPathContained -Path $dataRoot -AllowedRoot $defaults.LocalAppData -ParameterName "DataRootDeletion")
    Remove-RFQLongPathTree -Path $dataRoot
}
if ($RemoveConfiguration) {
    [void](Assert-RFQPathContained -Path $ConfigRoot -AllowedRoot $defaults.LocalAppData -ParameterName "ConfigRootDeletion")
    Remove-RFQLongPathTree -Path $ConfigRoot
}

[pscustomobject]@{
    Status = "uninstalled"
    ApplicationRemoved = $true
    LocalDataRemoved = [bool]$RemoveLocalData
    ConfigurationRemoved = [bool]$RemoveConfiguration
    ApiKeyRemoved = [bool]$RemoveConfiguration
    DefaultDataPolicy = "preserve"
}
