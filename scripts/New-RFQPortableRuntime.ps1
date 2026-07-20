[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BaseRuntimeZip,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$BaseRuntimeSha256,
    [Parameter(Mandatory = $true)][string]$Wheelhouse,
    [Parameter(Mandatory = $true)][string]$RequirementsLock,
    [Parameter(Mandatory = $true)][string]$BuildPython,
    [Parameter(Mandatory = $true)][string]$OutputRoot,
    [string[]]$ExpectedModules = @(
        "fastapi", "uvicorn", "pydantic", "multipart",
        "docx", "openpyxl", "fitz", "pypdf", "pdfplumber", "PIL"
    )
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

function Expand-RFQSafeZip {
    param([string]$Archive, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
    $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
    try {
        if ($zip.Entries.Count -gt 20000) { throw "Base runtime archive contains too many entries." }
        $total = [int64]0
        $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $zip.Entries) {
            $raw = ([string]$entry.FullName).Replace('/', '\').TrimEnd('\')
            if (-not $raw) { continue }
            $relative = Assert-RFQSafeRelativePath -Path $raw -ParameterName "RuntimeZipEntry"
            if (-not $seen.Add($relative)) { throw "Duplicate runtime archive entry: $relative" }
            $unixMode = ([int64]$entry.ExternalAttributes -shr 16) -band 0xF000
            if ($unixMode -eq 0xA000) { throw "Runtime archive cannot contain symbolic links." }
            $total += [int64]$entry.Length
            if ($total -gt 2147483648) { throw "Expanded base runtime exceeds the 2 GiB safety limit." }
            $target = Join-Path $Destination $relative
            [void](Assert-RFQPathContained -Path $target -AllowedRoot $Destination -ParameterName "RuntimeZipDestination")
            if ([string]::IsNullOrEmpty([string]$entry.Name)) {
                [IO.Directory]::CreateDirectory($target) | Out-Null
                continue
            }
            [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
            $inputStream = $entry.Open()
            $outputStream = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try { $inputStream.CopyTo($outputStream) }
            finally { $outputStream.Dispose(); $inputStream.Dispose() }
        }
    }
    finally { $zip.Dispose() }
}

$BaseRuntimeZip = Assert-RFQLocalAbsolutePath -Path $BaseRuntimeZip -ParameterName "BaseRuntimeZip"
$Wheelhouse = Assert-RFQLocalAbsolutePath -Path $Wheelhouse -ParameterName "Wheelhouse" -DisallowDriveRoot
$RequirementsLock = Assert-RFQLocalAbsolutePath -Path $RequirementsLock -ParameterName "RequirementsLock"
$BuildPython = Assert-RFQLocalAbsolutePath -Path $BuildPython -ParameterName "BuildPython"
$OutputRoot = Assert-RFQLocalAbsolutePath -Path $OutputRoot -ParameterName "OutputRoot" -DisallowDriveRoot
foreach ($leaf in @($BaseRuntimeZip, $RequirementsLock, $BuildPython)) {
    if (-not (Test-Path -LiteralPath $leaf -PathType Leaf)) { throw "Required local build input is missing." }
}
if (-not (Test-Path -LiteralPath $Wheelhouse -PathType Container)) { throw "Wheelhouse does not exist." }
if ([IO.Path]::GetExtension($BaseRuntimeZip) -ine ".zip") { throw "Base runtime must be a local ZIP archive." }
if ((Get-FileHash -LiteralPath $BaseRuntimeZip -Algorithm SHA256).Hash -ine $BaseRuntimeSha256) {
    throw "Base runtime ZIP SHA256 does not match the approved value."
}
if (Test-Path -LiteralPath $OutputRoot) { throw "OutputRoot must not already exist." }
foreach ($item in Get-ChildItem -LiteralPath $Wheelhouse -Recurse -Force) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Wheelhouse cannot contain reparse points." }
}
$wheelFiles = @(Get-ChildItem -LiteralPath $Wheelhouse -Filter "*.whl" -File)
if ($wheelFiles.Count -eq 0) { throw "Wheelhouse contains no wheel files." }

$parent = Assert-RFQLocalAbsolutePath -Path (Split-Path -Parent $OutputRoot) -ParameterName "OutputParent"
$stage = Join-Path $parent (".runtime-build-{0}" -f [Guid]::NewGuid().ToString('N'))
[void](Assert-RFQPathContained -Path $stage -AllowedRoot $parent -ParameterName "RuntimeBuildStage")
$pipReport = $null
try {
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    Expand-RFQSafeZip -Archive $BaseRuntimeZip -Destination $stage
    $python = Join-Path $stage "python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Base runtime ZIP does not contain root-level python.exe." }
    if (Test-Path -LiteralPath (Join-Path $stage "pyvenv.cfg")) { throw "Base runtime cannot be a virtual environment." }

    $pthFiles = @(Get-ChildItem -LiteralPath $stage -Filter "python*._pth" -File)
    if ($pthFiles.Count -ne 1) { throw "Python embeddable ZIP must contain exactly one python*._pth file." }
    $lines = @(Get-Content -LiteralPath $pthFiles[0].FullName -Encoding UTF8 | Where-Object {
        $_ -notmatch '^\s*#?\s*import site\s*$' -and
        $_ -notmatch '^\s*Lib[\\/]site-packages\s*$' -and
        $_ -notmatch '^\s*\.\.\s*$' -and
        $_ -notmatch '^\s*\.\.[\\/]translation\s*$'
    })
    $pthText = (($lines + @("Lib\site-packages", "..\translation", "..", "import site")) -join "`r`n") + "`r`n"
    [IO.File]::WriteAllText($pthFiles[0].FullName, $pthText, (New-Object Text.UTF8Encoding($false)))
    $sitePackages = Join-Path $stage "Lib\site-packages"
    [IO.Directory]::CreateDirectory($sitePackages) | Out-Null

    $pipReport = Join-Path $parent (".pip-report-{0}.json" -f [Guid]::NewGuid().ToString('N'))
    $oldNoIndex = $env:PIP_NO_INDEX
    $oldDisableCheck = $env:PIP_DISABLE_PIP_VERSION_CHECK
    try {
        $env:PIP_NO_INDEX = "1"
        $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
        $pipOutput = & $BuildPython -m pip install `
            --no-index `
            --require-hashes `
            --only-binary=:all: `
            --no-compile `
            --find-links $Wheelhouse `
            --requirement $RequirementsLock `
            --target $sitePackages `
            --report $pipReport 2>&1
        if ($LASTEXITCODE -ne 0) { throw "Offline hash-locked wheel installation failed." }
    }
    finally {
        $env:PIP_NO_INDEX = $oldNoIndex
        $env:PIP_DISABLE_PIP_VERSION_CHECK = $oldDisableCheck
        $pipOutput = $null
    }

    $runtimePrunedPaths = @("Lib\site-packages\onnx\backend\test")
    foreach ($relative in $runtimePrunedPaths) {
        $pruneTarget = Join-Path $stage $relative
        [void](Assert-RFQPathContained -Path $pruneTarget -AllowedRoot $stage -ParameterName "RuntimePruneTarget")
        if (Test-Path -LiteralPath $pruneTarget -PathType Container) {
            Remove-Item -LiteralPath $pruneTarget -Recurse -Force
        }
    }

    $zeroByteWheelMarkers = @(Get-ChildItem -LiteralPath $sitePackages -Recurse -File -Filter "*.whl" | Where-Object { $_.Length -eq 0 })
    foreach ($marker in $zeroByteWheelMarkers) { Remove-Item -LiteralPath $marker.FullName -Force }
    if (@(Get-ChildItem -LiteralPath $sitePackages -Recurse -File -Filter "*.whl").Count -gt 0) {
        throw "Installed runtime contains an unexpected nested wheel artifact."
    }
    if (@(Get-ChildItem -LiteralPath $sitePackages -Recurse -File -Filter "*.pyc").Count -gt 0 -or
        @(Get-ChildItem -LiteralPath $sitePackages -Recurse -Directory -Filter "__pycache__").Count -gt 0) {
        throw "Installed runtime contains generated Python cache artifacts."
    }

    $report = Get-Content -LiteralPath $pipReport -Raw -Encoding UTF8 | ConvertFrom-Json
    $packages = @($report.install | ForEach-Object {
        [ordered]@{
            name = [string]$_.metadata.name
            version = [string]$_.metadata.version
            archive_sha256 = if ($_.download_info.archive_info.hash -match '^sha256=(.+)$') { $Matches[1] } else { "unknown" }
        }
    } | Sort-Object name)
    if (@($packages | Where-Object { $_.archive_sha256 -eq "unknown" }).Count -gt 0) {
        throw "Pip report omitted a package archive SHA256."
    }
    Remove-Item -LiteralPath $pipReport -Force
    $pipReport = $null

    $probe = & (Join-Path $PSScriptRoot "Test-RFQPortableRuntime.ps1") -RuntimeRoot $stage -ExpectedModules $ExpectedModules -RelocationTest
    $runtimeVersion = (& $python -I -c "import platform; print(platform.python_version())").Trim()
    $manifest = [ordered]@{
        schema_version = "1.0"
        runtime_contract = "python_embeddable_zip_plus_offline_hash_locked_wheels"
        release_module_search_paths = @("..\translation", "..")
        python_version = $runtimeVersion
        architecture = "windows_x86_64"
        base_runtime_zip_sha256 = $BaseRuntimeSha256.ToLowerInvariant()
        requirements_lock_sha256 = (Get-FileHash -LiteralPath $RequirementsLock -Algorithm SHA256).Hash.ToLowerInvariant()
        network_used_during_build = $false
        virtual_environment = $false
        relocatable_probe = [bool]$probe.RelocationTested
        required_modules = $ExpectedModules
        pruned_nonruntime_paths = $runtimePrunedPaths
        packages = $packages
    }
    Write-RFQJson -Path (Join-Path $stage "runtime_build_manifest.json") -Value $manifest -Depth 10
    Move-Item -LiteralPath $stage -Destination $OutputRoot
    $stage = $null
    [pscustomobject]@{
        Status = "built"
        RuntimeRoot = $OutputRoot
        PythonVersion = $runtimeVersion
        PackageCount = $packages.Count
        RelocationTested = $true
        OfflineHashLocked = $true
    }
}
finally {
    if ($pipReport -and (Test-Path -LiteralPath $pipReport)) { Remove-Item -LiteralPath $pipReport -Force }
    if ($stage -and (Test-Path -LiteralPath $stage)) {
        [void](Assert-RFQPathContained -Path $stage -AllowedRoot $parent -ParameterName "RuntimeBuildCleanup")
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
}
