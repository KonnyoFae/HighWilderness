[CmdletBinding()]
param([switch]$BuildOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Join-Path $projectRoot 'apps\desktop'
$runtimeRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
$pythonCandidates = @($env:HIGH_WILDERNESS_PYTHON, (Join-Path $projectRoot '.venv\Scripts\python.exe'), (Join-Path $runtimeRoot 'python\python.exe'))
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) { $pythonCandidates += $pythonCommand.Source }
$pythonPath = $pythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $pythonPath) { throw '找不到 Python，请配置 HIGH_WILDERNESS_PYTHON 后重试。' }
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodePath = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $runtimeRoot 'node\bin\node.exe' }
if (-not (Test-Path -LiteralPath $nodePath)) { throw '找不到 Node.js。' }
$rustBinPath = Join-Path $env:USERPROFILE '.cargo\bin'
$env:Path = "$rustBinPath;$(Split-Path -Parent $nodePath);$env:Path"
$env:HIGH_WILDERNESS_PYTHON = $pythonPath
$env:HIGH_WILDERNESS_REPO_ROOT = $projectRoot
$env:HIGH_WILDERNESS_TESTBENCH = '1'
$executable = Join-Path $desktopRoot 'src-tauri\target\debug\high-wilderness-desktop.exe'
$stamp = Join-Path $projectRoot '.local\testbench-build.timestamp'
$sources = @(Get-ChildItem (Join-Path $desktopRoot 'src'), (Join-Path $desktopRoot 'src-tauri\src') -Recurse -File)
$sources += Get-Item (Join-Path $desktopRoot 'package.json'), (Join-Path $desktopRoot 'src-tauri\Cargo.toml'), (Join-Path $desktopRoot 'src-tauri\tauri.conf.json'), $PSCommandPath
$needsBuild = -not (Test-Path -LiteralPath $stamp) -or -not (Test-Path -LiteralPath $executable)
if (-not $needsBuild) { $needsBuild = [bool]($sources | Where-Object LastWriteTimeUtc -gt (Get-Item -LiteralPath $stamp).LastWriteTimeUtc | Select-Object -First 1) }
Push-Location $desktopRoot
try {
    if ($needsBuild) {
        Write-Host '正在构建战术测试台，首次启动需要稍候…'
        & $nodePath './node_modules/typescript/bin/tsc' -b
        if ($LASTEXITCODE -ne 0) { throw '界面检查失败。' }
        & $nodePath './node_modules/@tauri-apps/cli/tauri.js' build --debug --no-bundle
        if ($LASTEXITCODE -ne 0) { throw '测试台构建失败。' }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $stamp) | Out-Null
        Set-Content -LiteralPath $stamp -Value 'Native testbench build completed.'
    }
    if (-not $BuildOnly) {
        Write-Host '正在打开战术测试台…'
        Start-Process -FilePath $executable -WorkingDirectory $projectRoot -WindowStyle Normal -Wait
    }
} finally { Pop-Location }
