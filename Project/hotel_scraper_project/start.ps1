Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Hotel Scraper - Starting..." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectRoot = $PSScriptRoot

# Check if backend is setup
Write-Host "Checking backend setup..." -ForegroundColor Yellow
if (-not (Test-Path "$projectRoot\backend\venv")) {
    Write-Host "[X] Backend not setup!" -ForegroundColor Red
    Write-Host "  Please run: cd backend; .\setup-backend.ps1" -ForegroundColor Yellow
    Write-Host ""
    pause
    exit 1
}

if (-not (Test-Path "$projectRoot\backend\.env")) {
    Write-Host "[!] Warning: Backend .env file not found!" -ForegroundColor Yellow
    Write-Host "  Please create backend\.env with MySQL credentials" -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "[OK] Backend OK" -ForegroundColor Green

# Check if frontend is setup
Write-Host "Checking frontend setup..." -ForegroundColor Yellow
if (-not (Test-Path "$projectRoot\frontend\node_modules")) {
    Write-Host "[X] Frontend not setup!" -ForegroundColor Red
    Write-Host "  Please run: cd frontend; .\setup-frontend.ps1" -ForegroundColor Yellow
    Write-Host ""
    pause
    exit 1
}

Write-Host "[OK] Frontend OK" -ForegroundColor Green
Write-Host ""

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Starting Servers..." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Start Backend
Write-Host "Starting Backend (FastAPI)..." -ForegroundColor Yellow
$backendPath = Join-Path $projectRoot "backend"
Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
`$host.UI.RawUI.WindowTitle = 'Backend Server - FastAPI'
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  Backend Server (FastAPI)' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''
cd '$backendPath'
.\venv\Scripts\Activate.ps1
Write-Host 'Starting FastAPI server...' -ForegroundColor Yellow
Write-Host 'API: http://localhost:8000' -ForegroundColor White
Write-Host ''
python main.py
"@

Start-Sleep -Seconds 3

Write-Host "Starting Frontend (Vue.js)..." -ForegroundColor Yellow
$frontendPath = Join-Path $projectRoot "frontend"
Start-Process powershell -ArgumentList "-NoExit", "-Command", @"
`$host.UI.RawUI.WindowTitle = 'Frontend Server - Vue.js'
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  Frontend Server (Vue.js)' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''
cd '$frontendPath'
Write-Host 'Starting Vite dev server...' -ForegroundColor Yellow
Write-Host 'App: http://localhost:5173' -ForegroundColor White
Write-Host ''
npm run dev
"@

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Servers Starting!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Two PowerShell windows have been opened:" -ForegroundColor Cyan
Write-Host "  [1] Backend Server  -> http://localhost:8000" -ForegroundColor White
Write-Host "  [2] Frontend Server -> http://localhost:5173" -ForegroundColor White
Write-Host ""
Write-Host "API Documentation:" -ForegroundColor Cyan
Write-Host "  http://localhost:8000/docs" -ForegroundColor White
Write-Host ""
Write-Host "Tips:" -ForegroundColor Yellow
Write-Host "  - Wait ~5 seconds for servers to fully start" -ForegroundColor Gray
Write-Host "  - Open http://localhost:5173 in your browser" -ForegroundColor Gray
Write-Host "  - To stop: Close the PowerShell windows or press Ctrl+C" -ForegroundColor Gray
Write-Host ""
Write-Host "Press any key to exit this window..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
