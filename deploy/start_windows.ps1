<#
.SYNOPSIS
    「我在呢」Windows 启动脚本（支持演示模式与本地完整服务模式）。

.EXAMPLE
    .\deploy\start_windows.ps1 -Demo
    .\deploy\start_windows.ps1 -Demo -Scene low
    .\deploy\start_windows.ps1
#>
param(
    [switch]$Demo,
    [ValidateSet("ok", "talking", "charging", "low", "offline", "alert")]
    [string]$Scene = "ok",
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $RepoRoot "venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Host "[setup] 未发现 venv，正在创建虚拟环境并安装依赖..." -ForegroundColor Cyan
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv venv (Join-Path $RepoRoot "venv")
        & uv pip install --python $Py -r (Join-Path $RepoRoot "requirements.txt")
    } else {
        python -m venv (Join-Path $RepoRoot "venv")
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -r (Join-Path $RepoRoot "requirements.txt")
    }
}

# 记录本次启动临时写入的环境变量，退出时还原，避免污染当前 PowerShell 会话
$savedEnv = @{}
function Set-ScopedEnv([string]$Key, [string]$Val) {
    if (-not $savedEnv.ContainsKey($Key)) {
        $savedEnv[$Key] = [Environment]::GetEnvironmentVariable($Key, "Process")
    }
    [Environment]::SetEnvironmentVariable($Key, $Val, "Process")
}

$procs = @()
try {
    # 加载 .env（仅在非 Demo 模式且环境变量未预先设置时生效）
    $EnvFile = Join-Path $RepoRoot ".env"
    if ((-not $Demo) -and (Test-Path $EnvFile)) {
        Get-Content $EnvFile -Encoding UTF8 | ForEach-Object {
            $line = $_.Trim()
            if ($line -and (-not $line.StartsWith("#")) -and ($line.Contains("="))) {
                $kv = $line.Split([char[]]@('='), 2)
                $k = $kv[0].Trim()
                $v = $kv[1].Trim()
                if ($k -and (-not [Environment]::GetEnvironmentVariable($k, "Process"))) {
                    Set-ScopedEnv $k $v
                }
            }
        }
    }

    if ($Demo) {
        if (-not $DataDir) {
            $DataDir = Join-Path $RepoRoot "demo-data"
        }
        if (-not (Test-Path $DataDir) -or ((Get-ChildItem $DataDir -Force | Measure-Object).Count -eq 0)) {
            & $Py (Join-Path $RepoRoot "tools\demo_data.py") $DataDir
        }
        Set-ScopedEnv "COMPANION_DATA_DIR" $DataDir
        Write-Host "[demo] 启动演示桥接 (127.0.0.1:3096, scene=$Scene)..." -ForegroundColor Green
        $procs += Start-Process -FilePath $Py -ArgumentList @("tools\demo_bridge.py", "--scene", $Scene) -WorkingDirectory $RepoRoot -PassThru -NoNewWindow
        Write-Host "[demo] 启动管理台 (http://127.0.0.1:3093)..." -ForegroundColor Green
        & $Py (Join-Path $RepoRoot "server.py")
    } else {
        if ($DataDir) {
            Set-ScopedEnv "COMPANION_DATA_DIR" $DataDir
        }
        Write-Host "[start] 启动 xiaozhi_bridge (OTA :3096 / WS :3097 / Intercom :3098)..." -ForegroundColor Green
        $procs += Start-Process -FilePath $Py -ArgumentList @("-u", "xiaozhi_bridge.py") -WorkingDirectory $RepoRoot -PassThru -NoNewWindow
        Write-Host "[start] 启动 realtime_server (WS :3095)..." -ForegroundColor Green
        $procs += Start-Process -FilePath $Py -ArgumentList @("-u", "realtime_server.py") -WorkingDirectory $RepoRoot -PassThru -NoNewWindow
        Write-Host "[start] 启动管理台 server.py (http://127.0.0.1:3093)..." -ForegroundColor Green
        & $Py (Join-Path $RepoRoot "server.py")
    }
} finally {
    foreach ($p in $procs) {
        if ($p -and (-not $p.HasExited)) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($k in $savedEnv.Keys) {
        [Environment]::SetEnvironmentVariable($k, $savedEnv[$k], "Process")
    }
}
