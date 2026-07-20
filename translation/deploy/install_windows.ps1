[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$VenvPath = "",
    [switch]$SkipSelfCheck,
    [switch]$RegisterUserRuntime
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RequiredPythonMajor = 3
$RequiredPythonMinor = 12
$PinnedPipVersion = "26.1.2"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ("{0}失败，退出码 {1}。请检查上方错误后重试。" -f $Description, $LASTEXITCODE)
    }
}

function Resolve-BasePython {
    param([string]$RequestedPython)

    if ($RequestedPython) {
        if (-not (Test-Path -LiteralPath $RequestedPython -PathType Leaf)) {
            throw "指定的 Python 不存在。请用 -PythonExe 指向 Python 3.12 x64 的 python.exe。"
        }
        return (Resolve-Path -LiteralPath $RequestedPython).Path
    }

    $launcher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $candidate = & $launcher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) {
                return ([string]($candidate | Select-Object -Last 1)).Trim()
            }
        }
        catch {
            # py launcher 存在但未登记 3.12 时，继续检查 PATH 中的 python.exe。
        }
    }

    $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        return $pythonCommand.Source
    }

    throw "未找到 Python。请先安装 Python 3.12 x64，并勾选 py launcher，或使用 -PythonExe 指定路径。"
}

try {
    $DeployDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path
    $ModuleRoot = (Resolve-Path -LiteralPath (Join-Path $DeployDir "..")).Path
    $RequirementsFile = Join-Path $DeployDir "requirements-windows.lock.txt"
    $SelfCheckFile = Join-Path $DeployDir "runtime_self_check.py"

    if (-not (Test-Path -LiteralPath $RequirementsFile -PathType Leaf)) {
        throw "缺少固定依赖文件 requirements-windows.lock.txt。请确认部署材料完整。"
    }
    if (-not (Test-Path -LiteralPath $SelfCheckFile -PathType Leaf)) {
        throw "缺少运行时自检脚本 runtime_self_check.py。请确认部署材料完整。"
    }

    $gitCommand = Get-Command "git.exe" -ErrorAction SilentlyContinue
    if (-not $gitCommand) {
        throw "未找到 Git。固定提交安装需要 Git for Windows；安装后重新打开 PowerShell 再执行。"
    }

    $BasePython = Resolve-BasePython -RequestedPython $PythonExe
    $probeJson = & $BasePython -c "import json,platform,struct,sys; print(json.dumps({'major':sys.version_info.major,'minor':sys.version_info.minor,'bits':struct.calcsize('P')*8,'implementation':platform.python_implementation()}))"
    if ($LASTEXITCODE -ne 0 -or -not $probeJson) {
        throw "无法启动所选 Python。请确认安装未损坏且当前账号有执行权限。"
    }
    $probe = ([string]($probeJson | Select-Object -Last 1)) | ConvertFrom-Json
    if ($probe.major -ne $RequiredPythonMajor -or $probe.minor -ne $RequiredPythonMinor) {
        throw ("当前 Python 为 {0}.{1}；本锁定文件只验收 Python 3.12。请安装 3.12 x64 后重试。" -f $probe.major, $probe.minor)
    }
    if ($probe.bits -ne 64) {
        throw "检测到 32 位 Python；PDF/ONNX 运行时要求 64 位 Python 3.12。"
    }

    if ($VenvPath) {
        if ([System.IO.Path]::IsPathRooted($VenvPath)) {
            $VenvDir = [System.IO.Path]::GetFullPath($VenvPath)
        }
        else {
            $VenvDir = [System.IO.Path]::GetFullPath((Join-Path $DeployDir $VenvPath))
        }
    }
    else {
        $RuntimeRoot = if ($env:B_PDF_TRANSLATION_RUNTIME_DIR) {
            [System.IO.Path]::GetFullPath($env:B_PDF_TRANSLATION_RUNTIME_DIR)
        }
        else {
            $LocalDataRoot = if ($env:LOCALAPPDATA) {
                $env:LOCALAPPDATA
            }
            else {
                [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
            }
            Join-Path $LocalDataRoot "RFQTranslationTool\BRuntime"
        }
        try {
            [System.IO.Directory]::CreateDirectory($RuntimeRoot) | Out-Null
            $WriteProbe = Join-Path $RuntimeRoot (".write_probe_{0}.tmp" -f [Guid]::NewGuid().ToString("N"))
            [System.IO.File]::WriteAllText($WriteProbe, "ok")
            Remove-Item -LiteralPath $WriteProbe -Force
        }
        catch {
            throw ("默认运行时目录不可写：{0}。请将 B_PDF_TRANSLATION_RUNTIME_DIR 设置为当前账号可写的短路径后重试。" -f $RuntimeRoot)
        }
        $VenvDir = Join-Path $RuntimeRoot ".venv"
    }
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"

    Write-Host "[1/5] 检查 Python 与 Git：通过"
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        if (Test-Path -LiteralPath $VenvDir) {
            throw "目标虚拟环境目录已存在但不完整。请人工确认该目录后移走，再重新执行；脚本不会自动删除目录。"
        }
        Invoke-Checked -Description "创建虚拟环境" -Executable $BasePython -Arguments @("-m", "venv", $VenvDir)
    }
    Write-Host "[2/5] 当前用户运行时：就绪"

    $env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
    $env:PYTHONUTF8 = "1"
    Invoke-Checked -Description "固定 pip 版本" -Executable $VenvPython -Arguments @("-m", "pip", "install", "--upgrade", ("pip=={0}" -f $PinnedPipVersion))
    Invoke-Checked -Description "安装 B 翻译固定依赖" -Executable $VenvPython -Arguments @("-m", "pip", "install", "--require-virtualenv", "--upgrade-strategy", "only-if-needed", "-r", $RequirementsFile)
    Write-Host "[3/5] 固定依赖安装：完成"

    Write-Host "[4/5] 项目内 PDF 运行时钩子：随正式源码加载，无额外安装步骤"

    if (-not $SkipSelfCheck) {
        Invoke-Checked -Description "运行时自检" -Executable $VenvPython -Arguments @($SelfCheckFile, "--module-root", $ModuleRoot, "--requirements", $RequirementsFile)
        Write-Host "[5/5] 运行时自检：通过"
    }
    else {
        Write-Warning "已按参数跳过运行时自检；当前结果不能作为部署验收证据。"
        Write-Host "[5/5] 运行时自检：已跳过"
    }

    if ($RegisterUserRuntime) {
        [Environment]::SetEnvironmentVariable("B_PDF_TRANSLATION_PYTHON", $VenvPython, "User")
        Write-Host "已为当前 Windows 用户登记 B_PDF_TRANSLATION_PYTHON。新进程生效；服务账号需单独配置。"
    }

    Write-Host "安装完成。运行时位于：$VenvPython"
    Write-Host "未设置 B_PDF_TRANSLATION_PYTHON 时，正式 B 入口将自动使用该默认路径。"
    Write-Host "本脚本不会读取或回显 API Key。请按 README 通过部署主机密钥配置提供模型凭据。"
}
catch {
    Write-Error ("B 翻译运行时安装未完成：{0}" -f $_.Exception.Message)
    Write-Host "处理建议：保留上方完整错误，确认 Python 3.12 x64、Git、网络和目录权限后重试。"
    exit 1
}
