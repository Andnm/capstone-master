# Chay EDA Wave A tren warehouse 3 nguon, TACH KHOI harness (khong gioi han thoi gian), ghi log + dong exit=.
# Dung: powershell -File run_wave_a_detached.ps1 -Tag r1
param([Parameter(Mandatory = $true)][string]$Tag)
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$eda = 'D:\MSE\CAPSTONE\hotel-price-intelligence\eda'
$w = 'D:\MSE\CAPSTONE\hotel-price-intelligence\data\warehouse'
$o = 'D:\MSE\CAPSTONE\outputs\eda-3src-20261006'
Set-Location $eda
$log = "$o\wave_a_${Tag}.log"
"start $(Get-Date -Format 'HH:mm:ss') head=$(git rev-parse --short HEAD)" | Out-File $log -Encoding utf8
& "$eda\.venv\Scripts\python.exe" run_wave_a.py --timeout 7200 `
    --ownership-manifest "$w\ownership_manifest_20261004.json" `
    --cohort-history "$w\cohort_history_20260916.json" `
    --source-manifest "$w\source_manifest_20261004.json" `
    --report "$w\reports\b20261004_3src.json" *>> $log
"exit=$LASTEXITCODE end $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
