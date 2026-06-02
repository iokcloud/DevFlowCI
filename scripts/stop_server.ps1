# Stop DevFlow CI uvicorn on port 8000
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path $PSScriptRoot -Parent

function Stop-ProcSafe {
    param([int]$ProcId)
    if ($ProcId -gt 0) {
        Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Stopping DevFlow CI on port $Port..."
Write-Host ""

# 1) OwningProcess from TCP table
Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
    Where-Object { $_.State -eq "Listen" } |
    ForEach-Object {
        Write-Host "Kill TCP owner PID $($_.OwningProcess)"
        Stop-ProcSafe $_.OwningProcess
    }

# 2) netstat LISTENING PIDs
netstat -ano | Select-String ":$Port\s+.*LISTENING" | ForEach-Object {
    if ($_.Line -match "\s(\d+)\s*$") {
        $pid = [int]$Matches[1]
        Write-Host "Kill netstat PID $pid"
        Stop-ProcSafe $pid
    }
}

# 3) uvicorn main:app (direct)
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "uvicorn" -and $_.CommandLine -match "main:app" } |
    ForEach-Object {
        Write-Host "Kill uvicorn PID $($_.ProcessId)"
        Stop-ProcSafe $_.ProcessId
    }

# 4) uvicorn worker (multiprocessing spawn)
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "multiprocessing\.spawn.*spawn_main" } |
    ForEach-Object {
        Write-Host "Kill worker PID $($_.ProcessId)"
        Stop-ProcSafe $_.ProcessId
    }

# 5) Python processes started from this project directory
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -and $_.Name -match "^python" -and $_.CommandLine -match [regex]::Escape($root)
    } |
    ForEach-Object {
        Write-Host "Kill project python PID $($_.ProcessId)"
        Stop-ProcSafe $_.ProcessId
    }

Start-Sleep -Seconds 2

$alive = $false
try {
    Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -UseBasicParsing -TimeoutSec 2 | Out-Null
    $alive = $true
} catch {
    $alive = $false
}

if ($alive) {
    Write-Host ""
    Write-Host "Warning: service still responds on port $Port."
    Write-Host "Try closing the start.bat window manually, or run Task Manager to end python.exe."
} else {
    Write-Host ""
    Write-Host "Port $Port is free."
}

Write-Host ""
