[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [string[]]$ExpectedModules = @(
        "fastapi", "uvicorn", "pydantic", "multipart",
        "docx", "openpyxl", "fitz", "pypdf", "pdfplumber", "PIL"
    ),
    [switch]$RelocationTest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "Common-RFQWorkbench.ps1")

function Invoke-RFQRuntimeProbe {
    param([string]$Root, [string[]]$Modules, [string[]]$AllowedExternalPaths = @())
    $python = Join-Path $Root "python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Portable runtime is missing python.exe at its root." }
    $moduleJson = $Modules | ConvertTo-Json -Compress
    $modulePayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($moduleJson))
    $allowedJson = $AllowedExternalPaths | ConvertTo-Json -Compress
    $allowedPayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($allowedJson))
    $code = @'
import base64
import importlib
import json
import pathlib
import struct
import sys

root = pathlib.Path(sys.argv[1]).resolve()
modules = json.loads(base64.b64decode(sys.argv[2]).decode("utf-8"))
allowed_external = {
    str(pathlib.Path(value).resolve()).casefold()
    for value in json.loads(base64.b64decode(sys.argv[3]).decode("utf-8"))
}
result = {
    "prefix_matches": pathlib.Path(sys.prefix).resolve() == root,
    "base_prefix_matches": pathlib.Path(sys.base_prefix).resolve() == root,
    "executable_matches": pathlib.Path(sys.executable).resolve().parent == root,
    "architecture_bits": struct.calcsize("P") * 8,
    "imports": {},
    "outside_paths": [],
    "controlled_external_paths": [],
}
for name in modules:
    try:
        importlib.import_module(name)
        result["imports"][name] = True
    except Exception as exc:
        result["imports"][name] = False
for raw in sys.path:
    if not raw:
        continue
    candidate = pathlib.Path(raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        if str(candidate).casefold() in allowed_external:
            result["controlled_external_paths"].append(candidate.name)
        else:
            result["outside_paths"].append(candidate.name)
print(json.dumps(result, sort_keys=True))
'@
    $codePayload = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
    $bootstrap = 'import base64,sys;exec(base64.b64decode(sys.argv.pop(1)))'
    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $raw = & $python -I -B -c $bootstrap $codePayload $Root $modulePayload $allowedPayload 2>&1
        $probeExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($probeExitCode -ne 0 -or -not $raw) {
        $diagnostic = (($raw | ForEach-Object { [string]$_ }) -join " | ")
        throw "Portable runtime probe failed: $diagnostic"
    }
    $probe = ($raw -join "`n") | ConvertFrom-Json
    if (-not [bool]$probe.prefix_matches -or -not [bool]$probe.base_prefix_matches -or -not [bool]$probe.executable_matches) {
        throw "Python prefix or executable is not rooted in the portable runtime."
    }
    if ([int]$probe.architecture_bits -ne 64) { throw "A 64-bit Python runtime is required." }
    if (@($probe.outside_paths).Count -gt 0) { throw "Portable runtime sys.path escapes its own root." }
    foreach ($name in $Modules) {
        if (-not [bool]$probe.imports.$name) { throw "Portable runtime cannot import required module: $name" }
    }
    return $probe
}

$RuntimeRoot = Assert-RFQLocalAbsolutePath -Path $RuntimeRoot -ParameterName "RuntimeRoot" -DisallowDriveRoot
if (-not (Test-Path -LiteralPath $RuntimeRoot -PathType Container)) { throw "RuntimeRoot does not exist." }
if (@(Get-ChildItem -LiteralPath $RuntimeRoot -Filter "pyvenv.cfg" -Recurse -File -ErrorAction Stop).Count -gt 0) {
    throw "Virtual environments are not accepted as a portable release runtime."
}
foreach ($item in Get-ChildItem -LiteralPath $RuntimeRoot -Recurse -Force) {
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Portable runtime cannot contain reparse points."
    }
}
$pth = @(Get-ChildItem -LiteralPath $RuntimeRoot -Filter "python*._pth" -File)
if ($pth.Count -ne 1) { throw "Portable runtime must contain exactly one python*._pth file." }
$pthText = Get-Content -LiteralPath $pth[0].FullName -Raw -Encoding UTF8
if ($pthText -notmatch '(?im)^Lib[\\/]site-packages\s*$' -or $pthText -notmatch '(?im)^import site\s*$') {
    throw "Portable runtime ._pth must enable Lib/site-packages and import site."
}
$hasTranslationParent = $pthText -match '(?im)^\.\.[\\/]translation\s*$'
$hasReleaseParent = $pthText -match '(?im)^\.\.\s*$'
if ($hasTranslationParent -ne $hasReleaseParent) {
    throw "Portable runtime ._pth must declare both controlled release paths or neither."
}
$controlledPaths = @()
if ($hasTranslationParent) {
    $releaseParent = [System.IO.Path]::GetFullPath((Join-Path $RuntimeRoot ".."))
    $controlledPaths = @((Join-Path $releaseParent "translation"), $releaseParent)
}

$probe = Invoke-RFQRuntimeProbe -Root $RuntimeRoot -Modules $ExpectedModules -AllowedExternalPaths $controlledPaths
$relocated = $null
try {
    if ($RelocationTest) {
        $relocated = Join-Path ([string]$env:TEMP) ("rfq-runtime-relocation-{0}" -f [Guid]::NewGuid().ToString('N'))
        $relocated = Assert-RFQLocalAbsolutePath -Path $relocated -ParameterName "RelocationRoot" -DisallowDriveRoot
        Copy-Item -LiteralPath $RuntimeRoot -Destination $relocated -Recurse
        $relocatedControlledPaths = @()
        if ($hasTranslationParent) {
            $relocatedParent = [System.IO.Path]::GetFullPath((Join-Path $relocated ".."))
            $relocatedControlledPaths = @((Join-Path $relocatedParent "translation"), $relocatedParent)
        }
        [void](Invoke-RFQRuntimeProbe -Root $relocated -Modules $ExpectedModules -AllowedExternalPaths $relocatedControlledPaths)
    }
    [pscustomobject]@{
        Status = "pass"
        RuntimeKind = "python_embeddable_plus_locked_wheels"
        Python64Bit = $true
        PrefixContained = $true
        RequiredModuleCount = $ExpectedModules.Count
        RelocationTested = [bool]$RelocationTest
        PyVenvRejected = $true
        UnexpectedExternalSysPathCount = 0
        ControlledReleasePathCount = $controlledPaths.Count
    }
}
finally {
    if ($relocated -and (Test-Path -LiteralPath $relocated)) {
        [void](Assert-RFQLocalAbsolutePath -Path $relocated -ParameterName "RelocationCleanup" -DisallowDriveRoot)
        Remove-Item -LiteralPath $relocated -Recurse -Force
    }
}
