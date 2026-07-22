[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [string]$InstallRoot = "$env:LOCALAPPDATA\Programs\RFQTranslationWorkbench",
    [string]$DataRoot = "$env:LOCALAPPDATA\RFQTranslationTool\Data",
    [string]$ConfigRoot = "$env:LOCALAPPDATA\RFQTranslationTool\Config",
    [string]$ShortcutRoot = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\RFQ Translation Workbench",
    [int]$Port = 8008,
    [string]$Version = "0.1.0-alpha.2",
    [switch]$NoShortcuts,
    [switch]$AcceptThirdPartyDownloads
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$PythonVersion = "3.12.10"
$PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
$PythonSha256 = "67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb"
$Pdf2zhUrl = "https://codeload.github.com/PDFMathTranslate-next/PDFMathTranslate-next/zip/3538a8195d8379fe3fb4a0117c88d15c5b7b5e89"
$Pdf2zhSha256 = "1c0ae6ab7d0d106e28be332a3fc56aa588f71e63abc5fc68e9d7975e962c03ff"

function Assert-LocalPath([string]$Path, [string]$Name) {
    $raw = $Path.Trim()
    if (-not $raw -or $raw.StartsWith("\\") -or $raw.StartsWith("//") -or
        $raw.StartsWith("\??\") -or $raw.StartsWith("\\?\") -or $raw.StartsWith("\\.\") -or
        $raw -match '^[A-Za-z][A-Za-z0-9+.-]*://' -or $raw -match '^[A-Za-z][A-Za-z0-9_]*::' -or
        $raw -notmatch '^[A-Za-z]:[\\/]') {
        throw "$Name must be a drive-qualified local path. Network, URI, provider, device and relative paths are forbidden."
    }
    $full = [IO.Path]::GetFullPath($raw)
    $drive = New-Object IO.DriveInfo($full.Substring(0, 1))
    if ([string]$drive.DriveType -eq "Network") { throw "$Name cannot use a mapped network drive." }
    return $full.TrimEnd('\')
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-VerifiedDownload([string]$Uri, [string]$Destination, [string]$Sha256) {
    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Destination
    $actual = Get-Sha256 $Destination
    if ($actual -ne $Sha256) { throw "Downloaded file SHA-256 mismatch: $Uri" }
}

if (-not $AcceptThirdPartyDownloads) {
    Write-Host "This installer downloads Python 3.12.10 from python.org, fixed packages from PyPI, and pdf2zh-next from a fixed GitHub commit."
    Write-Host "Review THIRD_PARTY_NOTICES.md and rerun with -AcceptThirdPartyDownloads."
    exit 3
}

$SourceRoot = Assert-LocalPath $SourceRoot "SourceRoot"
$InstallRoot = Assert-LocalPath $InstallRoot "InstallRoot"
$DataRoot = Assert-LocalPath $DataRoot "DataRoot"
$ConfigRoot = Assert-LocalPath $ConfigRoot "ConfigRoot"
$ShortcutRoot = Assert-LocalPath $ShortcutRoot "ShortcutRoot"
foreach ($root in @($InstallRoot, $DataRoot, $ConfigRoot)) {
    if (-not $root.StartsWith([IO.Path]::GetFullPath($env:LOCALAPPDATA).TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Install, data and configuration roots must remain inside the current user's LocalAppData."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot "app\rfq_app\main.py") -PathType Leaf)) { throw "SourceRoot is incomplete." }

$downloadRoot = Join-Path $env:TEMP ("rfq-workbench-bootstrap-" + [Guid]::NewGuid().ToString("N"))
$stageRoot = Join-Path $env:LOCALAPPDATA ("RFQWB_Build\" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
$releaseRoot = Join-Path (Join-Path $InstallRoot "releases") $Version
New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Split-Path -Parent $stageRoot),(Split-Path -Parent $releaseRoot) -Force | Out-Null

try {
    $python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $pythonInstaller = Join-Path $downloadRoot "python-$PythonVersion-amd64.exe"
        Invoke-VerifiedDownload $PythonUrl $pythonInstaller $PythonSha256
        $pythonTarget = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"
        $arguments = @('/quiet', 'InstallAllUsers=0', "TargetDir=$pythonTarget", 'Include_launcher=0', 'PrependPath=0', 'Include_test=0', 'Shortcuts=0', 'Include_doc=0', 'Include_tcltk=0')
        $process = Start-Process -FilePath $pythonInstaller -ArgumentList $arguments -PassThru -Wait -WindowStyle Hidden
        if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw "Python installation failed with exit code $($process.ExitCode)."
        }
    }
    $versionCode = (& $python -c 'import sys;print(sys.version_info.major*100+sys.version_info.minor)').Trim()
    if ($versionCode -ne '312') { throw "Python 3.12 x64 is required; found version code $versionCode." }
    $detected = (& $python --version).Trim().Replace('Python ', '')

    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
    foreach ($name in @('app','pipeline','translation','parsing','parameter_cards','reports','templates')) {
        Copy-Item -LiteralPath (Join-Path $SourceRoot $name) -Destination (Join-Path $stageRoot $name) -Recurse -Force
    }
    & $python -m venv $stageRoot
    $venvPython = Join-Path $stageRoot "Scripts\python.exe"
    & $venvPython -m pip install --disable-pip-version-check --upgrade 'pip==25.1.1' 'setuptools==80.9.0' 'wheel==0.45.1'
    if ($LASTEXITCODE -ne 0) { throw "Bootstrapping pip failed." }

    $pdf2zhArchive = Join-Path $downloadRoot "pdf2zh-next-3538a8195d83.zip"
    Invoke-VerifiedDownload $Pdf2zhUrl $pdf2zhArchive $Pdf2zhSha256
    & $venvPython -m pip install --disable-pip-version-check --no-compile --no-deps $pdf2zhArchive
    if ($LASTEXITCODE -ne 0) { throw "Installing the fixed pdf2zh-next source failed." }

    $sourceLock = Join-Path $SourceRoot "translation\deploy\requirements-windows.lock.txt"
    $runtimeLock = Join-Path $downloadRoot "requirements-pypi.lock.txt"
    $lines = Get-Content -LiteralPath $sourceLock -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*pdf2zh-next\s*@' }
    [IO.File]::WriteAllLines($runtimeLock, $lines, (New-Object Text.UTF8Encoding($false)))
    & $venvPython -m pip install --disable-pip-version-check --no-compile -r $runtimeLock
    if ($LASTEXITCODE -ne 0) { throw "Installing fixed PyPI dependencies failed." }

    New-Item -ItemType Directory -Path (Join-Path $stageRoot "runtime") -Force | Out-Null
    Copy-Item -LiteralPath $venvPython -Destination (Join-Path $stageRoot "runtime\python.exe") -Force

    if (Test-Path -LiteralPath $releaseRoot) { throw "Release already exists: $releaseRoot" }
    Move-Item -LiteralPath $stageRoot -Destination $releaseRoot
    $stageRoot = $null

    $manifestFiles = @()
    foreach ($file in Get-ChildItem -LiteralPath $releaseRoot -Recurse -File -Force) {
        $relative = $file.FullName.Substring($releaseRoot.Length).TrimStart('\')
        if ($relative -eq 'pyvenv.cfg' -or $relative -match '^(Lib|Scripts|Include|share)\\') { continue }
        $manifestFiles += [ordered]@{ path = $relative; size = $file.Length; sha256 = Get-Sha256 $file.FullName }
    }
    $manifest = [ordered]@{
        schema_version = '1.0'; product = 'RFQ Translation Workbench'; version = $Version
        source_commit = '544c333facdfef94e99583093be7410142d446ac'
        distribution = 'windows-online-bootstrap'; runtime_layout = 'local_venv'; python = $detected
        requirements_lock_sha256 = Get-Sha256 $sourceLock; pdf2zh_source_sha256 = $Pdf2zhSha256
        files = @($manifestFiles | Sort-Object path)
    }
    [IO.File]::WriteAllText((Join-Path $releaseRoot 'release_manifest.json'), (($manifest | ConvertTo-Json -Depth 8) + "`n"), (New-Object Text.UTF8Encoding($false)))

    $installer = Join-Path $SourceRoot 'scripts\Install-RFQWorkbench.ps1'
    $installArgs = @{ PackageRoot=$releaseRoot; InstallRoot=$InstallRoot; DataRoot=$DataRoot; ConfigRoot=$ConfigRoot; ShortcutRoot=$ShortcutRoot; Port=$Port }
    if ($NoShortcuts) { $installArgs.NoShortcuts = $true }
    & $installer @installArgs
    if ($LASTEXITCODE -ne 0) { throw "Final installation registration failed." }

    Write-Host "Installed RFQ Translation Workbench $Version."
    Write-Host "Configure your own provider with scripts\Configure-RFQWorkbench.ps1, then start the local service."
}
finally {
    if ($stageRoot -and (Test-Path -LiteralPath $stageRoot)) { Remove-Item -LiteralPath $stageRoot -Recurse -Force }
    if (Test-Path -LiteralPath $downloadRoot) { Remove-Item -LiteralPath $downloadRoot -Recurse -Force }
}
