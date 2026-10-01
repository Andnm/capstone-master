$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path $PSScriptRoot).Path
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "scraper-frontend"
$python = Join-Path $backendRoot "venv\Scripts\python.exe"

$mysqlExe = "E:\Programs\mysql-aux\bin\mysqld.exe"
$mysqlConfig = "E:\Programs\mysql-aux\my.ini"
$nodeBin = "C:\Users\omach\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$pnpm = "C:\Users\omach\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

$requiredFiles = @(
    $python,
    $mysqlExe,
    $mysqlConfig,
    (Join-Path $nodeBin "node.exe"),
    $pnpm
)

foreach ($requiredFile in $requiredFiles) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Thieu file bat buoc: $requiredFile"
    }
}

$env:Path = "$nodeBin;$env:Path"
$env:PNPM_HOME = "E:\Programs\pnpm"
$env:PNPM_STORE_DIR = "E:\Programs\pnpm\store"
$env:WDM_LOCAL = "1"
$env:WDM_LOG = "0"

function Test-TcpPort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutMilliseconds = 500
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $result = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        return $result.AsyncWaitHandle.WaitOne($TimeoutMilliseconds, $false) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-TcpPort {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [int]$TimeoutSeconds = 60
    )

    for ($i = 0; $i -lt $TimeoutSeconds; $i++) {
        if (Test-TcpPort -Port $Port) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-WorkerHealth {
    try {
        return Invoke-RestMethod `
            -Uri "http://127.0.0.1:8000/api/scraper/worker/health" `
            -TimeoutSec 3
    }
    catch {
        return $null
    }
}

function Wait-WorkerOnline {
    param([int]$TimeoutSeconds = 60)

    for ($i = 0; $i -lt $TimeoutSeconds; $i++) {
        $health = Get-WorkerHealth
        if ($null -ne $health -and $health.online -eq $true) {
            return $health
        }
        Start-Sleep -Seconds 1
    }
    return $null
}

Write-Host "=== AUX CRAWLER STARTUP ===" -ForegroundColor Cyan

if (Test-TcpPort -Port 3306) {
    Write-Host "[OK] MySQL da chay tren port 3306."
}
else {
    Write-Host "[START] Dang bat MySQL..."
    Start-Process `
        -FilePath $mysqlExe `
        -ArgumentList "--defaults-file=$mysqlConfig" `
        -WindowStyle Hidden | Out-Null

    if (-not (Wait-TcpPort -Port 3306 -TimeoutSeconds 60)) {
        throw "MySQL khong san sang tren port 3306 sau 60 giay."
    }
    Write-Host "[OK] MySQL da san sang."
}

if (Test-TcpPort -Port 8000) {
    Write-Host "[OK] Backend da chay tren port 8000."
}
else {
    Write-Host "[START] Dang bat backend..."
    Start-Process `
        -FilePath $python `
        -ArgumentList "main.py" `
        -WorkingDirectory $backendRoot `
        -WindowStyle Hidden | Out-Null

    if (-not (Wait-TcpPort -Port 8000 -TimeoutSeconds 60)) {
        throw "Backend khong san sang tren port 8000 sau 60 giay."
    }
    Write-Host "[OK] Backend da san sang."
}

$workerHealth = Get-WorkerHealth
if ($null -ne $workerHealth -and $workerHealth.online -eq $true) {
    Write-Host "[OK] Worker dang online: $($workerHealth.worker_id)"
}
else {
    $workerProcesses = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and $_.CommandLine -match "run_worker\.py"
    }

    if ($workerProcesses) {
        Write-Host "[WAIT] Da co worker process; dang cho heartbeat..."
    }
    else {
        Write-Host "[START] Dang bat durable worker..."
        Start-Process `
            -FilePath $python `
            -ArgumentList "scripts\run_worker.py" `
            -WorkingDirectory $backendRoot `
            -WindowStyle Hidden | Out-Null
    }

    $workerHealth = Wait-WorkerOnline -TimeoutSeconds 60
    if ($null -eq $workerHealth) {
        throw "Worker khong online sau 60 giay. Khong tao crawl run cho den khi sua xong."
    }
    Write-Host "[OK] Worker dang online: $($workerHealth.worker_id)"
}

if (Test-TcpPort -Port 3000) {
    Write-Host "[OK] Frontend da chay tren port 3000."
}
else {
    Write-Host "[START] Dang bat frontend..."
    Start-Process `
        -FilePath $pnpm `
        -ArgumentList "run", "dev" `
        -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden | Out-Null

    if (-not (Wait-TcpPort -Port 3000 -TimeoutSeconds 90)) {
        throw "Frontend khong san sang tren port 3000 sau 90 giay."
    }
    Write-Host "[OK] Frontend da san sang."
}

$backendHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 5
if ($backendHealth.status -ne "ok") {
    throw "Backend health check khong tra status=ok."
}

$frontendResponse = Invoke-WebRequest `
    -Uri "http://127.0.0.1:3000" `
    -UseBasicParsing `
    -TimeoutSec 30

if ($frontendResponse.StatusCode -ne 200) {
    throw "Frontend health check tra HTTP $($frontendResponse.StatusCode)."
}

Write-Host ""
Write-Host "Tat ca da san sang." -ForegroundColor Green
Write-Host "Trang crawl: http://127.0.0.1:3000" -ForegroundColor Green
Write-Host "Launcher chi khoi dong he thong, KHONG tu tao crawl run."

Start-Process "http://127.0.0.1:3000"

