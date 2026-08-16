$projectRoot = (Resolve-Path $PSScriptRoot).Path
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "scraper-frontend"
$python = Join-Path $backendRoot "venv\Scripts\python.exe"

$backend = Start-Process -FilePath $python -ArgumentList "main.py" -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru
$worker = Start-Process -FilePath $python -ArgumentList "scripts\run_worker.py" -WorkingDirectory $backendRoot -WindowStyle Hidden -PassThru
$frontend = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" -WorkingDirectory $frontendRoot -WindowStyle Hidden -PassThru

Write-Host "Backend PID: $($backend.Id)"
Write-Host "Worker PID: $($worker.Id)"
Write-Host "Frontend PID: $($frontend.Id)"
Write-Host "Open http://127.0.0.1:3000"
