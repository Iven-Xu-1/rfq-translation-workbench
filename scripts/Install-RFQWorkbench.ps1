[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageRoot,
    [string]$InstallRoot = "",
    [string]$DataRoot = "",
    [string]$ConfigRoot = "",
    [string]$ShortcutRoot = "",
    [int]$Port = 8008,
    [switch]$NoShortcuts,
    [switch]$DoNotActivate
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

function ConvertTo-RFQExtendedLocalPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full.StartsWith("\\?\")) { return $full }
    if ($full.StartsWith("\\")) { throw "Internal release copy cannot use a UNC path." }
    if ($full -notmatch '^[A-Za-z]:[\\/]') { throw "Internal release copy requires a drive-qualified local path." }
    return "\\?\$full"
}

function Copy-RFQReleaseFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $sourceExtended = ConvertTo-RFQExtendedLocalPath -Path $Source
    $destinationExtended = ConvertTo-RFQExtendedLocalPath -Path $Destination
    [System.IO.File]::Copy($sourceExtended, $destinationExtended, $true)
}

function Copy-RFQReleaseToStage {
    param(
        [Parameter(Mandatory = $true)]$Contract,
        [Parameter(Mandatory = $true)][string]$StageRoot
    )
    foreach ($relative in @($Contract.Files)) {
        $source = Join-Path ([string]$Contract.Root) $relative
        $destination = Join-Path $StageRoot $relative
        [void](Assert-RFQPathContained -Path $destination -AllowedRoot $StageRoot -ParameterName "ReleaseDestination")
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        Copy-RFQReleaseFile -Source $source -Destination $destination
    }
    Copy-RFQReleaseFile -Source ([string]$Contract.ManifestPath) -Destination (Join-Path $StageRoot "release_manifest.json")
}

function Sync-RFQOpsScripts {
    param([Parameter(Mandatory = $true)][string]$TargetRoot)
    $ops = Join-Path $TargetRoot "ops"
    [System.IO.Directory]::CreateDirectory($ops) | Out-Null
    foreach ($name in $script:RFQOpsScriptNames) {
        $source = Join-Path $PSScriptRoot $name
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Installer script set is incomplete: $name" }
        $destination = Join-Path $ops $name
        [void](Assert-RFQPathContained -Path $destination -AllowedRoot $ops -ParameterName "OpsScriptDestination")
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function New-RFQScriptShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string]$ConfigPath
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = ('-NoProfile -File "{0}" -ConfigRoot "{1}"' -f $ScriptPath, $ConfigPath)
    $shortcut.WorkingDirectory = Split-Path -Parent $ScriptPath
    $shortcut.WindowStyle = 1
    $shortcut.Save()
}

$defaults = Get-RFQDefaultPaths
if (-not $InstallRoot) { $InstallRoot = $defaults.InstallRoot }
if (-not $DataRoot) { $DataRoot = $defaults.DataRoot }
if (-not $ConfigRoot) { $ConfigRoot = $defaults.ConfigRoot }
if (-not $ShortcutRoot) { $ShortcutRoot = $defaults.ShortcutRoot }

# Validate every caller-controlled path before the first filesystem access to it.
$PackageRoot = Assert-RFQLocalAbsolutePath -Path $PackageRoot -ParameterName "PackageRoot" -DisallowDriveRoot
$InstallRoot = Assert-RFQCurrentUserRoot -Path $InstallRoot -ParameterName "InstallRoot"
$DataRoot = Assert-RFQCurrentUserRoot -Path $DataRoot -ParameterName "DataRoot"
$ConfigRoot = Assert-RFQCurrentUserRoot -Path $ConfigRoot -ParameterName "ConfigRoot"
$ShortcutRoot = Assert-RFQShortcutRoot -Path $ShortcutRoot
if ((Test-RFQPathOverlap -First $InstallRoot -Second $DataRoot) -or
    (Test-RFQPathOverlap -First $InstallRoot -Second $ConfigRoot) -or
    (Test-RFQPathOverlap -First $DataRoot -Second $ConfigRoot)) {
    throw "Install, configuration, and data roots must not overlap."
}
if ($Port -lt 1024 -or $Port -gt 65535) { throw "Port must be between 1024 and 65535." }

$contract = Get-RFQReleaseContract -PackageRoot $PackageRoot
$releasesRoot = Join-Path $InstallRoot "releases"
$releaseRoot = Join-Path $releasesRoot ([string]$contract.Version)
[void](Assert-RFQPathContained -Path $releaseRoot -AllowedRoot $releasesRoot -ParameterName "ReleaseRoot")
[System.IO.Directory]::CreateDirectory($releasesRoot) | Out-Null

$stage = Join-Path $releasesRoot (".i-{0}" -f [Guid]::NewGuid().ToString('N').Substring(0, 12))
[void](Assert-RFQPathContained -Path $stage -AllowedRoot $releasesRoot -ParameterName "InstallStage")
try {
    if (Test-Path -LiteralPath $releaseRoot) {
        $existing = Get-RFQReleaseContract -PackageRoot $releaseRoot
        if ([string]$existing.ManifestSha256 -ne [string]$contract.ManifestSha256) {
            throw "A different release already uses version $($contract.Version)."
        }
    }
    else {
        [System.IO.Directory]::CreateDirectory($stage) | Out-Null
        Copy-RFQReleaseToStage -Contract $contract -StageRoot $stage
        $staged = Get-RFQReleaseContract -PackageRoot $stage
        if ([string]$staged.ManifestSha256 -ne [string]$contract.ManifestSha256) {
            throw "The staged release failed manifest verification."
        }
        Move-Item -LiteralPath $stage -Destination $releaseRoot
        $stage = $null
    }

    [System.IO.Directory]::CreateDirectory($ConfigRoot) | Out-Null
    [System.IO.Directory]::CreateDirectory($DataRoot) | Out-Null
    Sync-RFQOpsScripts -TargetRoot $InstallRoot

    $settingsPath = Join-Path $ConfigRoot "settings.json"
    $existingSettings = $null
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        $existingSettings = Get-RFQSettings -ConfigRoot $ConfigRoot
        if (-not ([string]$existingSettings.install_root).Equals($InstallRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not ([string]$existingSettings.data_root).Equals($DataRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Existing settings use different install or data roots."
        }
    }
    elseif ($DoNotActivate) {
        throw "DoNotActivate can only be used with an existing installation."
    }

    $providerBaseUrl = if ($existingSettings -and (Test-RFQHasProperty -Object $existingSettings -Name "provider_base_url")) { [string]$existingSettings.provider_base_url } else { "" }
    $providerModel = if ($existingSettings -and (Test-RFQHasProperty -Object $existingSettings -Name "provider_model")) { [string]$existingSettings.provider_model } else { "" }
    $activeVersion = if ($existingSettings) { [string]$existingSettings.release_version } else { [string]$contract.Version }
    $activeCommit = if ($existingSettings) { [string]$existingSettings.source_commit } else { "manifest:$(([string]$contract.ManifestSha256).Substring(0, 12))" }
    if (-not $DoNotActivate) {
        $activeVersion = [string]$contract.Version
        $activeCommit = if (Test-RFQHasProperty -Object $contract.Manifest -Name "source_commit") { [string]$contract.Manifest.source_commit } else { "manifest:$(([string]$contract.ManifestSha256).Substring(0, 12))" }
    }
    $settings = [ordered]@{
        schema_version = $script:RFQSettingsSchemaVersion
        product = $script:RFQProductName
        install_scope = "current_user"
        install_root = $InstallRoot
        config_root = $ConfigRoot
        data_root = $DataRoot
        listen_host = "127.0.0.1"
        port = $Port
        workers = 1
        release_version = $activeVersion
        source_commit = $activeCommit
        provider_base_url = $providerBaseUrl
        provider_model = $providerModel
        api_key_storage = "dpapi_current_user"
        autostart_enabled = $false
        updated_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    [void](Assert-RFQLoopbackSettings -Settings ([pscustomobject]$settings))
    Write-RFQJson -Path $settingsPath -Value $settings

    if (-not $DoNotActivate) {
        Write-RFQPointer -Path (Join-Path $ConfigRoot "active_release.txt") -Value $releaseRoot
    }

    if (-not $NoShortcuts -and -not $DoNotActivate) {
        [System.IO.Directory]::CreateDirectory($ShortcutRoot) | Out-Null
        $ops = Join-Path $InstallRoot "ops"
        New-RFQScriptShortcut -Path (Join-Path $ShortcutRoot "Start RFQ Translation Workbench.lnk") -ScriptPath (Join-Path $ops "Start-RFQWorkbench.ps1") -ConfigPath $ConfigRoot
        New-RFQScriptShortcut -Path (Join-Path $ShortcutRoot "Stop RFQ Translation Workbench.lnk") -ScriptPath (Join-Path $ops "Stop-RFQWorkbench.ps1") -ConfigPath $ConfigRoot
        New-RFQScriptShortcut -Path (Join-Path $ShortcutRoot "RFQ Translation Workbench Status.lnk") -ScriptPath (Join-Path $ops "Get-RFQWorkbenchStatus.ps1") -ConfigPath $ConfigRoot
        $urlText = "[InternetShortcut]`r`nURL=http://127.0.0.1:$Port/`r`n"
        Write-RFQTextUtf8NoBom -Path (Join-Path $ShortcutRoot "Open RFQ Translation Workbench.url") -Text $urlText
    }

    [pscustomobject]@{
        Status = "installed"
        Version = [string]$contract.Version
        ReleaseRoot = $releaseRoot
        Active = -not [bool]$DoNotActivate
        ListenHost = "127.0.0.1"
        Port = $Port
        Workers = 1
        AutostartEnabled = $false
        ApiKeyConfigured = Test-Path -LiteralPath (Join-Path $ConfigRoot "api_key.dpapi") -PathType Leaf
    }
}
finally {
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        [void](Assert-RFQPathContained -Path $stage -AllowedRoot $releasesRoot -ParameterName "InstallStageCleanup")
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
}
