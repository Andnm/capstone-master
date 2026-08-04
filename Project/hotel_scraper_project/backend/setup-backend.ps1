Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Backend Setup (FastAPI + MySQL)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/4] Checking Python..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Python not found"
    }
    Write-Host "[OK] Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[X] ERROR: Python not found. Please install Python 3.8+" -ForegroundColor Red
    Write-Host "  Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
    pause
    exit 1
}
Write-Host ""

Write-Host "[2/4] Creating virtual environment..." -ForegroundColor Yellow
if (Test-Path "venv") {
    Write-Host "Virtual environment already exists, skipping..." -ForegroundColor Gray
} else {
    python -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[X] ERROR: Failed to create virtual environment" -ForegroundColor Red
        pause
        exit 1
    }
    Write-Host "[OK] Virtual environment created" -ForegroundColor Green
}
Write-Host ""

Write-Host "[3/4] Installing Python packages..." -ForegroundColor Yellow
& ".\venv\Scripts\Activate.ps1"
pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[X] ERROR: Failed to install packages" -ForegroundColor Red
    pause
    exit 1
}
Write-Host "[OK] Packages installed" -ForegroundColor Green
Write-Host ""

Write-Host "[4/4] Creating .env file..." -ForegroundColor Yellow
if (Test-Path ".env") {
    Write-Host ".env file already exists, skipping..." -ForegroundColor Gray
} else {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "[OK] .env file created from .env.example" -ForegroundColor Green
        Write-Host ""
        Write-Host "[!] IMPORTANT: Please edit .env file with your MySQL credentials!" -ForegroundColor Yellow
        Write-Host "   Open: .env" -ForegroundColor White
        Write-Host "   Update: DB_PASSWORD=your_mysql_password" -ForegroundColor White
    } else {
        @"
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password_here
DB_NAME=hotel_scraper

API_HOST=0.0.0.0
API_PORT=8000
CORS_ORIGINS=http://localhost:5173
"@ | Out-File -FilePath ".env" -Encoding UTF8
        Write-Host "[OK] .env file created with default values" -ForegroundColor Green
        Write-Host ""
        Write-Host "[!] IMPORTANT: Please edit .env file with your MySQL credentials!" -ForegroundColor Yellow
        Write-Host "   Update: DB_PASSWORD=your_password_here" -ForegroundColor White
    }
}
Write-Host ""

Write-Host "========================================" -ForegroundColor Green
Write-Host "  Backend Setup Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Edit .env file with your MySQL password" -ForegroundColor White
Write-Host "2. Create MySQL database:" -ForegroundColor White
Write-Host "   mysql -u root -p -e 'CREATE DATABASE hotel_scraper'" -ForegroundColor Gray
Write-Host "3. Import schema:" -ForegroundColor White
Write-Host "   mysql -u root -p hotel_scraper < app/database/setup.sql" -ForegroundColor Gray
Write-Host "4. Start server:" -ForegroundColor White
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Gray
Write-Host "   python main.py" -ForegroundColor Gray
Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Gray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
