[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$BackendRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $BackendRoot 'venv\Scripts\python.exe'
$WorkerScript = Join-Path $BackendRoot 'scripts\run_worker.py'
$MySqlConfig = Join-Path $BackendRoot '.env.mysql.local'
$MySqlPhysicalRoot = 'C:\Users\dangn\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\hotel-price-intelligence'
$MySqlBaseDir = Join-Path $MySqlPhysicalRoot 'mysql-8.4.11-winx64'
$MySqlDataDir = Join-Path $MySqlPhysicalRoot 'mysql-data'
$MySqlErrorLog = Join-Path $MySqlPhysicalRoot 'mysql-error.log'
$MySqlExe = Join-Path $MySqlBaseDir 'bin\mysqld.exe'
$MySqlHost = '127.0.0.1'
$MySqlPort = 3306
$LogDirectory = Join-Path $BackendRoot 'crawl_artifacts'
$LogFile = Join-Path $LogDirectory 'worker_startup.log'

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

function Write-StartupLog {
    param([Parameter(Mandatory)][string]$Message)
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff zzz'), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Test-LocalTcpPort {
    param(
        [Parameter(Mandatory)][string]$Address,
        [Parameter(Mandatory)][int]$Port,
        [int]$TimeoutMilliseconds = 2000
    )
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect($Address, $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne($TimeoutMilliseconds)) {
            return $false
        }
        $client.EndConnect($pending)
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Assert-RequiredFile {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Label)
    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    }
    catch {
        Write-StartupLog "ERROR: Cannot access $Label at $Path ($($_.Exception.Message))"
        throw
    }
    if ($item.PSIsContainer) {
        Write-StartupLog "ERROR: Missing $Label at $Path"
        throw "Missing $Label at $Path"
    }
}

try {
    Write-StartupLog 'Bootstrap started.'
    Write-StartupLog "Runtime identity: $([Security.Principal.WindowsIdentity]::GetCurrent().Name)."
    Assert-RequiredFile -Path $PythonExe -Label 'virtualenv Python'
    Assert-RequiredFile -Path $WorkerScript -Label 'worker script'
    Assert-RequiredFile -Path $MySqlConfig -Label 'MySQL defaults file'
    Assert-RequiredFile -Path $MySqlExe -Label 'portable mysqld'

    if (Test-LocalTcpPort -Address $MySqlHost -Port $MySqlPort) {
        Write-StartupLog "MySQL is already reachable at ${MySqlHost}:$MySqlPort."
    }
    else {
        Write-StartupLog "MySQL is not reachable at ${MySqlHost}:$MySqlPort; starting portable mysqld."
        $mysqlProcess = Start-Process `
            -FilePath $MySqlExe `
            -ArgumentList @(
                "--defaults-file=$MySqlConfig",
                "--basedir=$MySqlBaseDir",
                "--datadir=$MySqlDataDir",
                "--log-error=$MySqlErrorLog"
            ) `
            -WorkingDirectory (Split-Path -Parent $MySqlExe) `
            -WindowStyle Hidden `
            -PassThru
        Write-StartupLog "Portable mysqld launch requested; PID=$($mysqlProcess.Id). Waiting up to 60 seconds."

        $deadline = (Get-Date).AddSeconds(60)
        do {
            Start-Sleep -Seconds 3
            if (Test-LocalTcpPort -Address $MySqlHost -Port $MySqlPort) {
                Write-StartupLog "MySQL is ready at ${MySqlHost}:$MySqlPort."
                break
            }
        } while ((Get-Date) -lt $deadline)

        if (-not (Test-LocalTcpPort -Address $MySqlHost -Port $MySqlPort)) {
            Write-StartupLog "ERROR: MySQL did not become reachable at ${MySqlHost}:$MySqlPort within 60 seconds. Worker will not start."
            exit 20
        }
    }

    $escapedWorkerPath = [regex]::Escape($WorkerScript)
    $existingWorkers = @(
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -match $escapedWorkerPath }
    )
    if ($existingWorkers.Count -gt 0) {
        $workerPids = ($existingWorkers.ProcessId -join ',')
        Write-StartupLog "Worker is already running (matching PIDs: $workerPids); no duplicate worker started."
        exit 0
    }

    Write-StartupLog "MySQL prerequisite passed; entering worker restart loop with $PythonExe."
    while ($true) {
        Write-StartupLog 'Starting run_worker.py.'
        & $PythonExe -u $WorkerScript 2>&1 |
            ForEach-Object { Write-StartupLog "[worker] $_" }
        $workerExitCode = $LASTEXITCODE
        Write-StartupLog "run_worker.py exited with code $workerExitCode. Waiting 5 seconds before restart."
        Start-Sleep -Seconds 5
    }
}
catch {
    Write-StartupLog "ERROR: $($_.Exception.Message)"
    exit 1
}
