# Ba kiem tra CHI DOC tren official (GPT file 25 §3 muc 4-5), tach khoi harness, moi script mot log + dong exit=.
#   1) compare_checksums rehearsal <-> official (semantic + exact 11 bang + provenance)
#   2) gate_inputs tren official
#   3) compare_wave_a (Wave A 2 nguon cu) vs official
param(
    [Parameter(Mandatory = $true)][string]$Rehearsal,   # database:batch
    [Parameter(Mandatory = $true)][string]$Official,    # database:batch
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
$offDb, $offBatch = $Official.Split(':')
function Run-Check($name, $argList) {
    $log = "$o\snapshots\check_${name}_${Tag}.log"
    "start $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8
    & $py @argList *>> $log
    "exit=$LASTEXITCODE end $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
}
Run-Check 'gate_inputs' @("$o\scripts\gate_inputs.py", '--database', $offDb, '--batch-id', $offBatch, '--ownership-manifest', "$w\ownership_manifest_20261004.json", '--cutoff-file', "$o\cutoff_20261004.json")
Run-Check 'compare_wave_a' @("$o\scripts\compare_wave_a.py", '--old', 'warehouse_20260916_2src:b20260916_2src', '--old-sources', 'local_primary,vps', '--new', $Official, '--new-sources', 'local_primary,vps,local_aux')
Run-Check 'compare_checksums_rh_vs_official' @("$o\scripts\compare_checksums.py", '--a', $Rehearsal, '--b', $Official)
"ALL DONE $(Get-Date -Format 'HH:mm:ss')" | Out-File "$o\snapshots\check_DONE_${Tag}.txt" -Encoding utf8
