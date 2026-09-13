[CmdletBinding()]
param([switch]$BuildOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Join-Path $projectRoot 'apps\desktop'
$runtimeRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonCandidates = @($env:HIGH_WILDERNESS_PYTHON, (Join-Path $projectRoot '.venv\Scripts\python.exe'), (Join-Path $runtimeRoot 'python\python.exe'))
if ($pythonCommand) { $pythonCandidates += $pythonCommand.Source }
$pythonPath = $pythonCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $pythonPath) { throw '找不到 Python。请安装 Python，或设置 HIGH_WILDERNESS_PYTHON 后重试。' }
$env:HIGH_WILDERNESS_PYTHON = $pythonPath
$env:HIGH_WILDERNESS_REPO_ROOT = $projectRoot
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$nodePath = if ($nodeCommand) { $nodeCommand.Source } else { Join-Path $runtimeRoot 'node\bin\node.exe' }
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
$executable = Join-Path $desktopRoot 'src-tauri\target\debug\high-wilderness-desktop.exe'
$stamp = Join-Path $projectRoot '.local\ship-editors-build.sha256'
# Include paths as well as content so deleted inputs also invalidate the build.
$sourceRoots = @((Join-Path $desktopRoot 'src'), (Join-Path $desktopRoot 'src-tauri\src'), (Join-Path $projectRoot 'contracts\web_bridge\fixtures'))
$sources = @(Get-ChildItem -LiteralPath $sourceRoots -Recurse -File)
$sources += Get-Item -LiteralPath @((Join-Path $desktopRoot 'package.json'), (Join-Path $desktopRoot 'package-lock.json'), (Join-Path $desktopRoot 'index.html'), (Join-Path $desktopRoot 'vite.config.ts'), (Join-Path $desktopRoot 'src-tauri\Cargo.toml'), (Join-Path $desktopRoot 'src-tauri\Cargo.lock'), (Join-Path $desktopRoot 'src-tauri\build.rs'), (Join-Path $desktopRoot 'src-tauri\tauri.conf.json'), $PSCommandPath)
$signature = ($sources | Sort-Object FullName | ForEach-Object { $_.FullName + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }) -join "`n"
$needsBuild = -not (Test-Path -LiteralPath $executable) -or -not (Test-Path -LiteralPath $stamp)
if (-not $needsBuild) { $needsBuild = (Get-Content -LiteralPath $stamp -Raw).TrimEnd() -ne $signature }
Push-Location $desktopRoot
try {
    if ($needsBuild) {
        if (-not (Test-Path -LiteralPath $nodePath)) { throw '首次构建需要 Node.js，请安装后重试。' }
        if (-not (Test-Path -LiteralPath 'node_modules/@tauri-apps/cli/tauri.js')) { throw '缺少桌面依赖。请先在 apps/desktop 中安装项目依赖。' }
        Write-Host '正在更新舰艇编辑器，首次构建需要稍候…'
        & $nodePath './node_modules/typescript/bin/tsc' -b
        if ($LASTEXITCODE -ne 0) { throw '界面检查失败，未启动旧程序。' }
        & $nodePath './node_modules/@tauri-apps/cli/tauri.js' build --debug --no-bundle
        if ($LASTEXITCODE -ne 0) { throw '编辑器构建失败。如编辑器正在运行，请先保存并关闭窗口，再重新启动。' }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $stamp) | Out-Null
        Set-Content -LiteralPath $stamp -Value $signature -Encoding UTF8
    }
    if (-not $BuildOnly) {
        Write-Host '正在打开舰艇编辑器…'
        Start-Process -FilePath $executable -ArgumentList '--editors' -WorkingDirectory $projectRoot -WindowStyle Hidden
    }
} finally { Pop-Location }
