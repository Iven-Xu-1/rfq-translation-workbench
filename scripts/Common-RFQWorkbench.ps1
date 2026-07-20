Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Security -ErrorAction Stop

$script:RFQProductName = "RFQ Translation Workbench"
$script:RFQSettingsSchemaVersion = "1.0"
$script:RFQRequiredReleaseFiles = @(
    "app\rfq_app\main.py",
    "pipeline\j_trial_pipeline.py",
    "translation\rfq_pdf_translation.py",
    "parsing\parser.py",
    "parameter_cards\run_d3_generation.py",
    "reports\run_export_d3.py",
    "templates\pump_parameter_card.docx",
    "runtime\python.exe"
)
$script:RFQOpsScriptNames = @(
    "Common-RFQWorkbench.ps1",
    "Install-RFQWorkbench.ps1",
    "Configure-RFQWorkbench.ps1",
    "Start-RFQWorkbench.ps1",
    "Watch-RFQWorkbench.ps1",
    "Stop-RFQWorkbench.ps1",
    "Get-RFQWorkbenchStatus.ps1",
    "Update-RFQWorkbench.ps1",
    "Rollback-RFQWorkbench.ps1",
    "Uninstall-RFQWorkbench.ps1"
)

function ConvertTo-RFQNativeLongPath {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $full = [System.IO.Path]::GetFullPath($Path)
    $prefix = -join @([char]92, [char]92, [char]63, [char]92)
    if ($full.StartsWith($prefix)) { return $full }
    return $prefix + $full
}

function Get-RFQFileSha256 {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $native = ConvertTo-RFQNativeLongPath -Path $Path
    $stream = [System.IO.File]::Open($native, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return (($algorithm.ComputeHash($stream) | ForEach-Object { $_.ToString("x2") }) -join "")
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Remove-RFQLongPathTree {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)

    $native = ConvertTo-RFQNativeLongPath -Path $Path
    if ([System.IO.File]::Exists($native)) {
        [System.IO.File]::SetAttributes($native, [System.IO.FileAttributes]::Normal)
        [System.IO.File]::Delete($native)
        return
    }
    if (-not [System.IO.Directory]::Exists($native)) { return }

    foreach ($file in [System.IO.Directory]::EnumerateFiles(
        $native,
        "*",
        [System.IO.SearchOption]::AllDirectories
    )) {
        [System.IO.File]::SetAttributes($file, [System.IO.FileAttributes]::Normal)
    }
    [System.IO.Directory]::Delete($native, $true)
}

function Get-RFQDriveDescriptor {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$DriveLetter)

    $drive = Get-PSDrive -Name $DriveLetter -PSProvider FileSystem -ErrorAction SilentlyContinue
    if (-not $drive) { return $null }
    try { $driveInfo = New-Object System.IO.DriveInfo("${DriveLetter}:\") }
    catch { return $null }
    return [pscustomobject]@{
        ProviderName = [string]$drive.Provider.Name
        DriveType = [string]$driveInfo.DriveType
        Root = [string]$drive.Root
        DisplayRoot = [string]$drive.DisplayRoot
    }
}

function Assert-RFQLocalAbsolutePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ParameterName = "Path",
        [switch]$DisallowDriveRoot
    )

    $raw = ([string]$Path).Trim()
    if ([string]::IsNullOrWhiteSpace($raw)) { throw "$ParameterName cannot be empty." }

    # This string-only boundary must run before any filesystem or provider call.
    if ($raw.StartsWith("\\") -or $raw.StartsWith("//") -or
        $raw.StartsWith("\??\") -or $raw.StartsWith("\\?\") -or
        $raw.StartsWith("\\.\")) {
        throw "$ParameterName must be a local absolute path; UNC, network, and device paths are forbidden."
    }
    if ($raw -match '^[A-Za-z][A-Za-z0-9+.-]*://') { throw "$ParameterName cannot be a URI." }
    if ($raw -match '^[A-Za-z][A-Za-z0-9_]*::') { throw "$ParameterName cannot use a PowerShell provider." }
    if ($raw -notmatch '^[A-Za-z]:[\\/]') { throw "$ParameterName must be a drive-qualified local absolute path." }
    if ($raw.Substring(2) -match ':') { throw "$ParameterName cannot contain an alternate data stream." }

    foreach ($segment in @($raw.Substring(3) -split '[\\/]')) {
        if (-not $segment) { continue }
        $base = ($segment.TrimEnd(' ', '.') -split '\.')[0]
        if ($base -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') {
            throw "$ParameterName contains a reserved Windows device name."
        }
    }

    try { $full = [System.IO.Path]::GetFullPath($raw) }
    catch { throw "$ParameterName is not a valid local path: $($_.Exception.Message)" }

    $driveLetter = $full.Substring(0, 1).ToUpperInvariant()
    $descriptor = Get-RFQDriveDescriptor -DriveLetter $driveLetter
    if (-not $descriptor -or $descriptor.ProviderName -ne "FileSystem") {
        throw "$ParameterName is not on a local FileSystem drive."
    }
    if ($descriptor.DriveType -eq "Network" -or -not [string]::IsNullOrWhiteSpace($descriptor.DisplayRoot)) {
        throw "$ParameterName cannot use a mapped network drive."
    }
    if ($descriptor.DriveType -notin @("Fixed", "Removable", "Ram")) {
        throw "$ParameterName uses a disallowed drive type: $($descriptor.DriveType)."
    }

    $root = [System.IO.Path]::GetPathRoot($full)
    if ($DisallowDriveRoot -and $full.TrimEnd('\') -eq $root.TrimEnd('\')) {
        throw "$ParameterName cannot be a drive root."
    }
    return $full
}

function Assert-RFQSafeRelativePath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ParameterName = "RelativePath"
    )

    $raw = ([string]$Path).Trim().Replace('/', '\').TrimEnd('\')
    if ([string]::IsNullOrWhiteSpace($raw) -or [System.IO.Path]::IsPathRooted($raw) -or
        $raw.StartsWith('\') -or $raw -match ':') {
        throw "$ParameterName must be a safe relative path."
    }
    $safe = New-Object System.Collections.Generic.List[string]
    foreach ($segment in @($raw -split '\\')) {
        if ([string]::IsNullOrWhiteSpace($segment) -or $segment -in @('.', '..')) {
            throw "$ParameterName contains an empty or traversal segment."
        }
        if ($segment.EndsWith(' ') -or $segment.EndsWith('.') -or
            $segment.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0) {
            throw "$ParameterName contains an unsafe Windows file name."
        }
        $base = ($segment -split '\.')[0]
        if ($base -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') {
            throw "$ParameterName contains a reserved Windows device name."
        }
        $safe.Add($segment)
    }
    return ($safe -join '\')
}

function Assert-RFQPathContained {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$AllowedRoot,
        [string]$ParameterName = "Path",
        [switch]$AllowRoot
    )

    $candidate = Assert-RFQLocalAbsolutePath -Path $Path -ParameterName $ParameterName
    $root = Assert-RFQLocalAbsolutePath -Path $AllowedRoot -ParameterName "AllowedRoot"
    $isRoot = $candidate.TrimEnd('\').Equals($root.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase)
    $rootPrefix = $root.TrimEnd('\') + '\'
    if ((-not $AllowRoot -and $isRoot) -or
        (-not $isRoot -and -not $candidate.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase))) {
        throw "$ParameterName must remain inside $root."
    }
    return $candidate
}

function Test-RFQPathOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$First,
        [Parameter(Mandatory = $true)][string]$Second
    )
    $a = (Assert-RFQLocalAbsolutePath -Path $First -ParameterName "FirstPath").TrimEnd('\')
    $b = (Assert-RFQLocalAbsolutePath -Path $Second -ParameterName "SecondPath").TrimEnd('\')
    return $a.Equals($b, [StringComparison]::OrdinalIgnoreCase) -or
        $a.StartsWith($b + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $b.StartsWith($a + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Get-RFQDefaultPaths {
    if ([string]::IsNullOrWhiteSpace([string]$env:LOCALAPPDATA)) {
        throw "LOCALAPPDATA is required for a current-user installation."
    }
    if ([string]::IsNullOrWhiteSpace([string]$env:APPDATA)) {
        throw "APPDATA is required for current-user shortcuts."
    }
    $local = Assert-RFQLocalAbsolutePath -Path ([string]$env:LOCALAPPDATA) -ParameterName "LOCALAPPDATA"
    $roaming = Assert-RFQLocalAbsolutePath -Path ([string]$env:APPDATA) -ParameterName "APPDATA"
    return [pscustomobject]@{
        LocalAppData = $local
        InstallRoot = Join-Path $local "Programs\RFQTranslationWorkbench"
        DataRoot = Join-Path $local "RFQTranslationTool\Data"
        ConfigRoot = Join-Path $local "RFQTranslationTool\Config"
        ShortcutRoot = Join-Path $roaming "Microsoft\Windows\Start Menu\Programs\RFQ Translation Workbench"
    }
}

function Assert-RFQCurrentUserRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ParameterName
    )
    $defaults = Get-RFQDefaultPaths
    $resolved = Assert-RFQLocalAbsolutePath -Path $Path -ParameterName $ParameterName -DisallowDriveRoot
    [void](Assert-RFQPathContained -Path $resolved -AllowedRoot $defaults.LocalAppData -ParameterName $ParameterName)
    return $resolved
}

function Assert-RFQShortcutRoot {
    param([Parameter(Mandatory = $true)][string]$Path)
    $defaults = Get-RFQDefaultPaths
    $root = Assert-RFQLocalAbsolutePath -Path $Path -ParameterName "ShortcutRoot" -DisallowDriveRoot
    [void](Assert-RFQPathContained -Path $root -AllowedRoot (Assert-RFQLocalAbsolutePath -Path ([string]$env:APPDATA) -ParameterName "APPDATA") -ParameterName "ShortcutRoot")
    return $root
}

function Write-RFQTextUtf8NoBom {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )
    $target = Assert-RFQLocalAbsolutePath -Path $Path -ParameterName "OutputPath"
    $parent = Split-Path -Parent $target
    [System.IO.Directory]::CreateDirectory($parent) | Out-Null
    $temp = Join-Path $parent (".{0}.{1}.tmp" -f [System.IO.Path]::GetFileName($target), [Guid]::NewGuid().ToString('N'))
    [void](Assert-RFQPathContained -Path $temp -AllowedRoot $parent -ParameterName "TemporaryOutput")
    try {
        [System.IO.File]::WriteAllText($temp, $Text, (New-Object System.Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temp -Destination $target -Force
    }
    finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}

function Write-RFQJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$Value,
        [int]$Depth = 10
    )
    $json = (($Value | ConvertTo-Json -Depth $Depth) -replace "`r`n", "`n")
    Write-RFQTextUtf8NoBom -Path $Path -Text ($json + "`n")
}

function Write-RFQPointer {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Value
    )
    Write-RFQTextUtf8NoBom -Path $Path -Text ($Value.Trim() + "`n")
}

function Test-RFQHasProperty {
    param([Parameter(Mandatory = $true)]$Object, [Parameter(Mandatory = $true)][string]$Name)
    return $null -ne $Object.PSObject.Properties[$Name]
}

function Get-RFQReleaseContract {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$PackageRoot)

    $root = Assert-RFQLocalAbsolutePath -Path $PackageRoot -ParameterName "PackageRoot" -DisallowDriveRoot
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw "PackageRoot does not exist." }
    $rootItem = Get-Item -LiteralPath $root -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "PackageRoot cannot be a reparse point."
    }
    foreach ($item in Get-ChildItem -LiteralPath $root -Recurse -Force) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release packages cannot contain reparse points: $($item.FullName)"
        }
    }

    $manifestPath = Join-Path $root "release_manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "release_manifest.json is required." }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($field in @("schema_version", "product", "version", "files")) {
        if (-not (Test-RFQHasProperty -Object $manifest -Name $field)) { throw "Release manifest is missing $field." }
    }
    if ([string]$manifest.schema_version -ne "1.0") { throw "Unsupported release manifest schema." }
    if ([string]$manifest.product -ne $script:RFQProductName) { throw "Unexpected release product." }
    $version = ([string]$manifest.version).Trim()
    if ($version -notmatch '^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$') { throw "Release version is unsafe." }

    $declared = @{}
    foreach ($entry in @($manifest.files)) {
        foreach ($field in @("path", "size", "sha256")) {
            if (-not (Test-RFQHasProperty -Object $entry -Name $field)) { throw "Release file entry is missing $field." }
        }
        $relative = Assert-RFQSafeRelativePath -Path ([string]$entry.path) -ParameterName "ReleaseManifestPath"
        if ($relative -ieq "release_manifest.json") { throw "release_manifest.json cannot declare itself." }
        if ($declared.ContainsKey($relative)) { throw "Duplicate release file entry: $relative" }
        $sizeText = [string]$entry.size
        $sha = ([string]$entry.sha256).ToLowerInvariant()
        if ($sizeText -notmatch '^\d+$') { throw "Invalid file size for $relative." }
        if ($sha -notmatch '^[0-9a-f]{64}$') { throw "Invalid SHA256 for $relative." }
        $declared[$relative] = [pscustomobject]@{ Size = [int64]$sizeText; Sha256 = $sha }
    }

    $runtimeLayout = if (Test-RFQHasProperty -Object $manifest -Name "runtime_layout") { [string]$manifest.runtime_layout } else { "embedded" }
    if ($runtimeLayout -notin @("embedded", "local_venv")) { throw "Unsupported runtime layout." }
    $actual = @{}
    foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File -Force) {
        $relative = Assert-RFQSafeRelativePath -Path $file.FullName.Substring($root.Length).TrimStart('\') -ParameterName "ReleaseFile"
        if ($relative -ieq "release_manifest.json") { continue }
        if ($runtimeLayout -eq "local_venv" -and
            ($relative -ieq "pyvenv.cfg" -or $relative -match '^(Lib|Scripts|Include|share)\\')) { continue }
        if ($actual.ContainsKey($relative)) { throw "Duplicate physical release path: $relative" }
        $actual[$relative] = $file
    }
    if ($actual.Count -ne $declared.Count) { throw "Release package file set does not match release_manifest.json." }
    foreach ($relative in $actual.Keys) {
        if (-not $declared.ContainsKey($relative)) { throw "Unmanifested release file: $relative" }
    }
    foreach ($relative in $declared.Keys) {
        if (-not $actual.ContainsKey($relative)) { throw "Missing release file: $relative" }
        $file = $actual[$relative]
        $expected = $declared[$relative]
        if ([int64]$file.Length -ne [int64]$expected.Size) { throw "Release file size mismatch: $relative" }
        $hash = Get-RFQFileSha256 -Path $file.FullName
        if ($hash -ne $expected.Sha256) { throw "Release file hash mismatch: $relative" }
    }
    foreach ($required in $script:RFQRequiredReleaseFiles) {
        if (-not $declared.ContainsKey($required)) { throw "Required release file is missing: $required" }
    }
    return [pscustomobject]@{
        Root = $root
        ManifestPath = $manifestPath
        Manifest = $manifest
        Version = $version
        Files = @($declared.Keys | Sort-Object)
        ManifestSha256 = Get-RFQFileSha256 -Path $manifestPath
    }
}

function Assert-RFQLoopbackSettings {
    param([Parameter(Mandatory = $true)]$Settings)
    foreach ($field in @("listen_host", "port", "workers")) {
        if (-not (Test-RFQHasProperty -Object $Settings -Name $field)) { throw "Settings are missing $field." }
    }
    if ([string]$Settings.listen_host -ne "127.0.0.1") { throw "Only 127.0.0.1 is permitted." }
    $port = [int]$Settings.port
    if ($port -lt 1024 -or $port -gt 65535) { throw "Port must be between 1024 and 65535." }
    if ([int]$Settings.workers -ne 1) { throw "Exactly one Uvicorn worker is required." }
    return $true
}

function Get-RFQSettings {
    param([Parameter(Mandatory = $true)][string]$ConfigRoot)
    $root = Assert-RFQCurrentUserRoot -Path $ConfigRoot -ParameterName "ConfigRoot"
    $path = Join-Path $root "settings.json"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Settings were not found." }
    $settings = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($field in @("schema_version", "product", "install_root", "config_root", "data_root", "listen_host", "port", "workers")) {
        if (-not (Test-RFQHasProperty -Object $settings -Name $field)) { throw "Settings are missing $field." }
    }
    if ([string]$settings.schema_version -ne $script:RFQSettingsSchemaVersion -or
        [string]$settings.product -ne $script:RFQProductName) {
        throw "Settings do not belong to this product or schema."
    }
    $install = Assert-RFQCurrentUserRoot -Path ([string]$settings.install_root) -ParameterName "settings.install_root"
    $config = Assert-RFQCurrentUserRoot -Path ([string]$settings.config_root) -ParameterName "settings.config_root"
    $data = Assert-RFQCurrentUserRoot -Path ([string]$settings.data_root) -ParameterName "settings.data_root"
    if (-not $config.Equals($root, [StringComparison]::OrdinalIgnoreCase)) { throw "ConfigRoot does not match settings." }
    if ((Test-RFQPathOverlap -First $install -Second $config) -or
        (Test-RFQPathOverlap -First $install -Second $data) -or
        (Test-RFQPathOverlap -First $config -Second $data)) {
        throw "Install, configuration, and data roots must not overlap."
    }
    [void](Assert-RFQLoopbackSettings -Settings $settings)
    return $settings
}

function Get-RFQActiveRelease {
    param([Parameter(Mandatory = $true)]$Settings)
    $install = Assert-RFQCurrentUserRoot -Path ([string]$Settings.install_root) -ParameterName "settings.install_root"
    $releases = Join-Path $install "releases"
    $pointer = Join-Path ([string]$Settings.config_root) "active_release.txt"
    if (-not (Test-Path -LiteralPath $pointer -PathType Leaf)) { throw "The active release pointer is missing." }
    $release = Assert-RFQLocalAbsolutePath -Path ((Get-Content -LiteralPath $pointer -Raw -Encoding UTF8).Trim()) -ParameterName "ActiveRelease"
    [void](Assert-RFQPathContained -Path $release -AllowedRoot $releases -ParameterName "ActiveRelease")
    if (-not (Test-Path -LiteralPath $release -PathType Container)) { throw "The active release directory is missing." }
    [void](Get-RFQReleaseContract -PackageRoot $release)
    return $release
}

function Get-RFQStatePath {
    param([Parameter(Mandatory = $true)]$Settings, [Parameter(Mandatory = $true)][string]$Name)
    $configRoot = Assert-RFQCurrentUserRoot -Path ([string]$Settings.config_root) -ParameterName "settings.config_root"
    $stateRoot = Join-Path $configRoot "state"
    [System.IO.Directory]::CreateDirectory($stateRoot) | Out-Null
    return (Join-Path $stateRoot $Name)
}

function Get-RFQKeyPath {
    param([Parameter(Mandatory = $true)]$Settings)
    $configRoot = Assert-RFQCurrentUserRoot -Path ([string]$Settings.config_root) -ParameterName "settings.config_root"
    return (Join-Path $configRoot "api_key.dpapi")
}

function ConvertFrom-RFQSecureString {
    param([Parameter(Mandatory = $true)][Security.SecureString]$SecureValue)
    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    }
}

function Protect-RFQSecret {
    param(
        [Parameter(Mandatory = $true)][Security.SecureString]$SecureValue,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $target = Assert-RFQLocalAbsolutePath -Path $Destination -ParameterName "SecretDestination"
    $plain = ConvertFrom-RFQSecureString -SecureValue $SecureValue
    if ([string]::IsNullOrWhiteSpace($plain)) { throw "API Key cannot be empty." }
    $bytes = $null
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($plain)
        $protected = [Security.Cryptography.ProtectedData]::Protect(
            $bytes,
            [Text.Encoding]::UTF8.GetBytes($script:RFQProductName),
            [Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
        [System.IO.File]::WriteAllBytes($target, $protected)
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
        $acl = New-Object Security.AccessControl.FileSecurity
        $acl.SetOwner($identity)
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, "FullControl", "Allow")
        [void]$acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $target -AclObject $acl
    }
    finally {
        if ($bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
        $plain = $null
    }
}

function Unprotect-RFQSecret {
    param([Parameter(Mandatory = $true)][string]$Source)
    $path = Assert-RFQLocalAbsolutePath -Path $Source -ParameterName "SecretSource"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "The protected API Key is not configured." }
    $protected = [System.IO.File]::ReadAllBytes($path)
    $bytes = [Security.Cryptography.ProtectedData]::Unprotect(
        $protected,
        [Text.Encoding]::UTF8.GetBytes($script:RFQProductName),
        [Security.Cryptography.DataProtectionScope]::CurrentUser
    )
    try { return [Text.Encoding]::UTF8.GetString($bytes) }
    finally { [Array]::Clear($bytes, 0, $bytes.Length) }
}

function Assert-RFQProviderSettings {
    param([Parameter(Mandatory = $true)]$Settings)
    foreach ($field in @("provider_base_url", "provider_model")) {
        if (-not (Test-RFQHasProperty -Object $Settings -Name $field) -or
            [string]::IsNullOrWhiteSpace([string]$Settings.$field)) {
            throw "First-run configuration is incomplete: $field is required."
        }
    }
    $uri = $null
    if (-not [Uri]::TryCreate([string]$Settings.provider_base_url, [UriKind]::Absolute, [ref]$uri)) {
        throw "provider_base_url must be an absolute URI."
    }
    if ($uri.Scheme -notin @("https", "http")) { throw "Only HTTP(S) model endpoints are supported." }
    if ($uri.Scheme -eq "http" -and $uri.Host -notin @("127.0.0.1", "localhost", "::1")) {
        throw "Plain HTTP is permitted only for a loopback model endpoint."
    }
    $keyPath = Get-RFQKeyPath -Settings $Settings
    if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) { throw "The API Key has not been configured." }
    return $true
}

function Get-RFQManagedProcess {
    param([Parameter(Mandatory = $true)][string]$PidPath)
    $path = Assert-RFQLocalAbsolutePath -Path $PidPath -ParameterName "PidPath"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
    try {
        $processId = [int]((Get-Content -LiteralPath $path -Raw -Encoding ASCII).Trim())
        return Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $processId) -ErrorAction SilentlyContinue
    }
    catch { return $null }
}

function Test-RFQProcessOwned {
    param(
        [Parameter(Mandatory = $true)]$Process,
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)][ValidateSet("watchdog", "service")][string]$Kind
    )
    $commandLine = [string]$Process.CommandLine
    if ($Kind -eq "watchdog") {
        return $commandLine -like "*Watch-RFQWorkbench.ps1*" -and
            $commandLine -like ("*{0}*" -f [string]$Settings.config_root)
    }
    return $commandLine -like "*uvicorn*rfq_app.main:app*" -and $commandLine -like "*--workers*1*"
}

function Stop-RFQProcessTree {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)
    $processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $children = @{}
    foreach ($process in $processes) {
        $parentId = [int]$process.ParentProcessId
        if (-not $children.ContainsKey($parentId)) { $children[$parentId] = @() }
        $children[$parentId] += $process
    }
    $ordered = New-Object System.Collections.Generic.List[int]
    function Add-RFQChildren {
        param([int]$ParentId)
        if ($children.ContainsKey($ParentId)) {
            foreach ($child in @($children[$ParentId])) {
                Add-RFQChildren -ParentId ([int]$child.ProcessId)
                $ordered.Add([int]$child.ProcessId)
            }
        }
    }
    Add-RFQChildren -ParentId $RootProcessId
    $ordered.Add($RootProcessId)
    foreach ($processId in $ordered) { Stop-Process -Id $processId -ErrorAction SilentlyContinue }
}

function Get-RFQHealthUri {
    param([Parameter(Mandatory = $true)]$Settings)
    [void](Assert-RFQLoopbackSettings -Settings $Settings)
    return ("http://127.0.0.1:{0}/api/health" -f [int]$Settings.port)
}

function Invoke-RFQHealth {
    param([Parameter(Mandatory = $true)]$Settings, [int]$TimeoutSeconds = 5)
    try { return Invoke-RestMethod -Uri (Get-RFQHealthUri -Settings $Settings) -Method Get -TimeoutSec $TimeoutSeconds }
    catch { return $null }
}

function Set-RFQProcessEnvironment {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)][string]$ReleaseRoot
    )
    $release = Assert-RFQLocalAbsolutePath -Path $ReleaseRoot -ParameterName "ReleaseRoot"
    $env:PYTHONUTF8 = "1"
    $env:RFQ_INSTALL_ROOT = $release
    $env:RFQ_PROJECT_DATA_ROOT = [string]$Settings.data_root
    $env:RFQ_J_PIPELINE_PATH = Join-Path $release "pipeline\j_trial_pipeline.py"
    $env:RFQ_C_PARSER_PATH = Join-Path $release "parsing\parser.py"
    $env:RFQ_B_TRANSLATOR_PATH = Join-Path $release "translation\rfq_pdf_translation.py"
    $env:RFQ_D3_RUNNER_PATH = Join-Path $release "parameter_cards\run_d3_generation.py"
    $env:RFQ_F_RUNNER_PATH = Join-Path $release "reports\run_export_d3.py"
    $env:RFQ_PARAMETER_CARD_TEMPLATE = Join-Path $release "templates\pump_parameter_card.docx"
    $env:RFQ_RUNTIME_PYTHON = Join-Path $release "runtime\python.exe"
    $env:RFQ_ENABLE_HOST_FOLDER_OPEN = "0"
    $env:RFQ_ENABLE_SERVER_PATH_IMPORT = "0"
    $env:RFQ_UVICORN_WORKERS = "1"
    $env:WEB_CONCURRENCY = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    $env:PYTHONNOUSERSITE = "1"
    $env:RFQ_APP_VERSION = [string]$Settings.release_version
    $env:RFQ_APP_COMMIT = [string]$Settings.source_commit
    $env:VECTOR_ENGINE_BASE_URL = [string]$Settings.provider_base_url
    $env:VECTOR_ENGINE_MODEL = [string]$Settings.provider_model
}
