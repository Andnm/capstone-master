# Chay boi Task Scheduler ngay luc may khoi dong xong (AtStartup, logon type S4U -
# KHONG can ai dang nhap), de tu dong tiep tuc hang doi crawl con dang do ke ca khi
# may tu restart giua dem luc khong ai thuc de dang nhap. Cho MySQL service san sang
# truoc khi chay worker de tranh race dieu kien luc moi boot (MySQL co the chua kip
# khoi dong xong ngay khi Task Scheduler ban trigger AtStartup).

$backendRoot = "D:\MSE\CAPSTONE\hotel-price-intelligence\backend"
$logDir = Join-Path $backendRoot "crawl_artifacts"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$logFile = Join-Path $logDir "startup_task_launch.log"

function Write-Log($msg) {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg" | Out-File -FilePath $logFile -Append -Encoding utf8
}

function Test-Port($ComputerName, $Port, $TimeoutMs = 1000) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $result = $client.BeginConnect($ComputerName, $Port, $null, $null)
        $ok = $result.AsyncWaitHandle.WaitOne($TimeoutMs, $false) -and $client.Connected
        $client.Close()
        return [bool]$ok
    } catch {
        return $false
    }
}

Write-Log "Startup task fired. Cho MySQL (127.0.0.1:3306) san sang..."

$mysqlReady = $false
for ($i = 0; $i -lt 30; $i++) {
    if (Test-Port -ComputerName "127.0.0.1" -Port 3306) { $mysqlReady = $true; break }
    Start-Sleep -Seconds 2
}

if (-not $mysqlReady) {
    Write-Log "MySQL khong san sang sau 60 giay - DUNG, khong chay run_worker.py."
    exit 1
}

Write-Log "MySQL san sang. Bat run_worker.py..."
Set-Location $backendRoot
& "$backendRoot\venv\Scripts\python.exe" "$backendRoot\scripts\run_worker.py" *>> $logFile
