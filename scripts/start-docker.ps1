# Start Docker Desktop safely on this machine, then bring up the ThanPhai stack.
#
# Why: every Windows shutdown/sleep leaves stale AF_UNIX socket files in %LOCALAPPDATA%\Docker\run that
# nothing can open or delete ("The file cannot be accessed by the system"). Docker Desktop then crashes
# with "rename ...sailor-ingest.sock ... .stale". Renaming the folder aside works; nothing is deleted.
#
# Usage (PowerShell, from the repo root):  .\scripts\start-docker.ps1

$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 turns a native command's stderr into an error under "Stop", so probe through cmd.
function Test-DockerEngine { cmd /c "docker info >nul 2>&1"; return ($LASTEXITCODE -eq 0) }
$dockerDir = Join-Path $env:LOCALAPPDATA "Docker"
$run = Join-Path $dockerDir "run"
$exe = Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"
if (-not (Test-Path $exe)) { $exe = "C:\Program Files\Docker\Docker\Docker Desktop.exe" }

if (Test-DockerEngine) {
    Write-Output "Docker is already running."
} else {
    if (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue) {
        Write-Output "Docker Desktop is starting or stuck; stopping it first."
        Get-Process "Docker Desktop", "com.docker.backend", "com.docker.build" -ErrorAction SilentlyContinue | Stop-Process -Force
        Start-Sleep 3
    }
    if (Test-Path $run) {
        $stale = Get-ChildItem $run -Force | Where-Object { $_.Attributes -match "ReparsePoint" }
        if ($stale) {
            $aside = "run.broken-" + (Get-Date -Format "yyyyMMdd-HHmmss")
            Rename-Item $run $aside
            Write-Output "Moved $($stale.Count) stale socket file(s) aside: $aside"
        }
    }
    Start-Process $exe
    Write-Output "Waiting for the Docker engine..."
    $deadline = (Get-Date).AddMinutes(5)
    do {
        Start-Sleep 5
        $up = Test-DockerEngine
    } until ($up -or (Get-Date) -gt $deadline)
    if (-not $up) { throw "Docker engine did not start within 5 minutes. Open Docker Desktop to see why." }
    Write-Output "Docker is running."
}

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    cmd /c "docker compose up -d 2>&1"
    if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
} finally { Pop-Location }
