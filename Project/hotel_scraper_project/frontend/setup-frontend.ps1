Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Frontend Setup (Vue.js + PrimeVue)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/2] Checking Node.js..." -ForegroundColor Yellow
try {
    $nodeVersion = node --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Node.js not found"
    }
    Write-Host "[OK] Found: Node.js $nodeVersion" -ForegroundColor Green
    
    $npmVersion = npm --version 2>&1
    Write-Host "[OK] Found: npm $npmVersion" -ForegroundColor Green
} catch {
    Write-Host "[X] ERROR: Node.js not found. Please install Node.js 20+" -ForegroundColor Red
    Write-Host "  Download from: https://nodejs.org/" -ForegroundColor Yellow
    pause
    exit 1
}
Write-Host ""

Write-Host "[2/2] Creating .env file..." -ForegroundColor Yellow
if (Test-Path ".env") {
    Write-Host ".env file already exists, skipping..." -ForegroundColor Gray
} else {
    @"
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
"@ | Out-File -FilePath ".env" -Encoding UTF8
    Write-Host "[OK] .env file created" -ForegroundColor Green
}
Write-Host ""

Write-Host "========================================" -ForegroundColor Green
Write-Host "  Frontend Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Install dependencies:" -ForegroundColor White
Write-Host "   npm install" -ForegroundColor Gray
Write-Host "2. Start development server:" -ForegroundColor White
Write-Host "   npm run dev" -ForegroundColor Gray
Write-Host ""
Write-Host "Server will run at:" -ForegroundColor Cyan
Write-Host "  http://localhost:5173" -ForegroundColor White
Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
