# Ba SQL-path smoke CHI DOC tren rehearsal (GPT file 20 §2), tach khoi harness, moi script mot log + dong exit=.
# Dung: powershell -File run_smokes_detached.ps1 -Database warehouse_rh20261004_3src -BatchId b20261004_3srcrh -Tag full_rehearsal_r2
param(
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$BatchId,
    [Parameter(Mandatory = $true)][string]$Tag
)
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$backend = 'D:\MSE\CAPSTONE\hotel-price-intelligence\backend'
$o = 'D:\MSE\CAPSTONE\outputs\warehouse-rebuild-20261004'
$w = 'D:\MSE\CAPSTONE\hotel-price-intelligence\data\warehouse'
Set-Location $backend
$py = 'venv\Scripts\python.exe'
function Run-Smoke($name, $argList) {
    $log = "$o\snapshots\smoke_${name}_${Tag}.log"
    "start $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8
    & $py @argList *>> $log
    "exit=$LASTEXITCODE end $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
}
Run-Smoke 'compare_checksums_self' @("$o\scripts\compare_checksums.py", '--a', "${Database}:${BatchId}", '--b', "${Database}:${BatchId}")
Run-Smoke 'gate_inputs' @("$o\scripts\gate_inputs.py", '--database', $Database, '--batch-id', $BatchId, '--ownership-manifest', "$w\ownership_manifest_20261004.json", '--cutoff-file', "$o\cutoff_20261004.json")
Run-Smoke 'compare_wave_a' @("$o\scripts\compare_wave_a.py", '--old', 'warehouse_20260916_2src:b20260916_2src', '--old-sources', 'local_primary,vps', '--new', "${Database}:${BatchId}", '--new-sources', 'local_primary,vps,local_aux')
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File "$o\snapshots\smoke_DONE_${Tag}.txt" -Encoding utf8
