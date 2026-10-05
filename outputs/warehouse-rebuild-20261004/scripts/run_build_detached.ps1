# Chay build_warehouse TACH KHOI harness (khong gioi han thoi gian), ghi log + dau hieu ket thuc.
# Dung: powershell -File run_build_detached.ps1 -Tag full_rehearsal_r2 -Database warehouse_rh20261004_3src -BatchId b20261004_3srcrh -Profile full
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$BatchId,
    [Parameter(Mandatory = $true)][ValidateSet('aux', 'full')][string]$Profile
)
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$backend = 'D:\MSE\CAPSTONE\hotel-price-intelligence\backend'
$o = 'D:\MSE\CAPSTONE\outputs\warehouse-rebuild-20261004'
$w = 'D:\MSE\CAPSTONE\hotel-price-intelligence\data\warehouse'
Set-Location $backend
$suffix = if ($Profile -eq 'aux') { '_auxonly' } else { '' }
$log = "$o\snapshots\build_${Tag}.log"
"start $(Get-Date -Format 'HH:mm:ss') db=$Database batch=$BatchId profile=$Profile head=$(git rev-parse --short HEAD)" | Out-File $log -Encoding utf8
& 'venv\Scripts\python.exe' scripts\build_warehouse.py --database $Database --batch-id $BatchId --base-dir ..\.. `
    --source-manifest "$w\source_manifest_20261004$suffix.json" `
    --cohort-manifest "$w\cohort_history_20260916.json" `
    --ownership-manifest "$w\ownership_manifest_20261004$suffix.json" *>> $log
"exit=$LASTEXITCODE end $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
