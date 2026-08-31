[CmdletBinding()]
param(
    [ValidateSet('check', 'install', 'web-check', 'rust-check', 'all-check', 'dev', 'tauri-dev')]
    [string]$Action = 'check'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Join-Path $projectRoot 'apps\desktop'

function Resolve-RequiredCommand([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "找不到必需命令：$Name"
    }
    return $command.Source
}

function Resolve-RustCommand([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    $profilePath = [Environment]::GetFolderPath('UserProfile')
    $candidate = Join-Path $profilePath ".cargo\bin\$Name.exe"
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "找不到 Rust 命令：$Name"
    }
    return $candidate
}

$pythonPath = Resolve-RequiredCommand 'python'
$nodePath = Resolve-RequiredCommand 'node'
$nodeRoot = Split-Path -Parent $nodePath
$npmCliPath = Join-Path $nodeRoot 'node_modules\npm\bin\npm-cli.js'
if (-not (Test-Path -LiteralPath $npmCliPath)) {
    throw "找不到 npm CLI：$npmCliPath"
}
$cargoPath = Resolve-RustCommand 'cargo'
$rustcPath = Resolve-RustCommand 'rustc'
$rustBinPath = Split-Path -Parent $cargoPath
$env:HIGH_WILDERNESS_PYTHON = $pythonPath
$env:HIGH_WILDERNESS_REPO_ROOT = $projectRoot
$env:CARGO = $cargoPath
$env:RUSTC = $rustcPath
if (($env:Path -split ';') -notcontains $rustBinPath) {
    $env:Path = "$rustBinPath;$env:Path"
}

function Invoke-Npm([string[]]$Arguments) {
    & $nodePath $npmCliPath @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-Cargo([string[]]$Arguments) {
    & $cargoPath @Arguments
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Show-Environment {
    [ordered]@{
        interface = 'gaotian.web-toolchain-check/v1'
        python = (& $pythonPath --version 2>&1 | Out-String).Trim()
        node = (& $nodePath --version | Out-String).Trim()
        npm = (& $nodePath $npmCliPath --version | Out-String).Trim()
        rustc = (& $rustcPath --version | Out-String).Trim()
        cargo = (& $cargoPath --version | Out-String).Trim()
        python_path = $pythonPath
        node_path = $nodePath
        npm_cli_path = $npmCliPath
        rustc_path = $rustcPath
        cargo_path = $cargoPath
        status = 'PASS'
    } | ConvertTo-Json
}

Push-Location $desktopRoot
try {
    switch ($Action) {
        'check' { Show-Environment }
        'install' { Invoke-Npm @('install') }
        'web-check' { Invoke-Npm @('run', 'check') }
        'rust-check' { Invoke-Cargo @('test', '--manifest-path', 'src-tauri\Cargo.toml') }
        'all-check' {
            Invoke-Npm @('run', 'check')
            Invoke-Cargo @('test', '--manifest-path', 'src-tauri\Cargo.toml')
            & $pythonPath -X utf8 (Join-Path $projectRoot '高天荒野舰艇测试总入口.py')
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
        'dev' { Invoke-Npm @('run', 'dev') }
        'tauri-dev' {
            $tauriCli = Join-Path $desktopRoot 'node_modules\@tauri-apps\cli\tauri.js'
            if (-not (Test-Path -LiteralPath $tauriCli)) { throw '尚未安装 Tauri CLI' }
            & $nodePath $tauriCli dev
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
    }
}
finally {
    Pop-Location
}
