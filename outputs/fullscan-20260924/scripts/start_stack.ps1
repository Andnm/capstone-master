# Khởi động API (cổng 8012) + worker cho lần quét cô lập, ghi PID vào run\stack_pids.json. Claude, 2026-09-25.
# Chạy (PowerShell):  powershell -File outputs\fullscan-20260924\scripts\start_stack.ps1 -Tag a1
# DB/upload/artifact đặt bằng biến môi trường (ghi đè .env) nên DB vận hành KHÔNG bị chạm. Worker mới = phiên Chrome mới ở mục đầu tiên.
param([string]$Tag = "a1")
$ErrorActionPreference = 'Stop'
$root = 'D:\MSE\CAPSTONE'
$backend = "$root\hotel-price-intelligence\backend"
$run = "$root\outputs\fullscan-20260924\run"
$pidFile = "$run\stack_pids.json"
if (Test-Path $pidFile) { throw "$pidFile exists - run stop_stack.ps1 first" }
$listen = Get-NetTCPConnection -LocalPort 8012 -State Listen -ErrorAction SilentlyContinue
if ($listen) { throw "port 8012 already in use by PID $($listen.OwningProcess)" }

$env:PYTHONIOENCODING = 'utf-8'
$env:DB_NAME = 'hotel_price_intel_fullscan_20260924'
$env:UPLOAD_DIR = "$run\uploaded_files"
$env:ARTIFACT_DIR = "$run\crawl_artifacts"
$py = "$backend\venv\Scripts\python.exe"

$api = Start-Process -FilePath $py -ArgumentList '-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8012', '--log-level', 'info' `
    -WorkingDirectory $backend -PassThru -WindowStyle Hidden -RedirectStandardOutput "$run\api_$Tag.log" -RedirectStandardError "$run\api_$Tag.err.log"
$wk = Start-Process -FilePath $py -ArgumentList 'scripts\run_worker.py' `
    -WorkingDirectory $backend -PassThru -WindowStyle Hidden -RedirectStandardOutput "$run\worker_$Tag.log" -RedirectStandardError "$run\worker_$Tag.err.log"
@{
    tag    = $Tag
    started = (Get-Date).ToString('o')
    api    = @{ pid = $api.Id; start = $api.StartTime.ToString('o') }
    worker = @{ pid = $wk.Id; start = $wk.StartTime.ToString('o') }
} | ConvertTo-Json | Set-Content -Path $pidFile -Encoding utf8

$ok = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 2
    try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8012/api/scraper/runs?limit=1' -UseBasicParsing -TimeoutSec 5; if ($r.StatusCode -eq 200) { $ok = $true; break } } catch { }
}
"API pid=$($api.Id) worker pid=$($wk.Id) | API ready: $ok"
if (-not $ok) { exit 1 }
