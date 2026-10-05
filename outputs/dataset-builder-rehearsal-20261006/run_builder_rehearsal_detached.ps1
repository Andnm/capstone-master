# Rehearsal dataset builder tren ban SAO cua warehouse hien hanh, TACH KHOI harness, khong gioi han thoi gian.
# CHI chay sau khi luot cao trong ngay xong va DB van hanh 0 active (nguyen tac no-overlap voi tac vu ghi nang).
# Dung: powershell -File run_builder_rehearsal_detached.ps1 -Tag rh1 -Source warehouse_20261004_3src -Target warehouse_dsdev_20261004_3src -Version ds_20261006_rh1
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Target,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$AnomalyCutoff = '2026-10-05T00:00:00Z'
)
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'
$ml = 'D:\MSE\CAPSTONE\hotel-price-intelligence\ml'
$o = 'D:\MSE\CAPSTONE\outputs\dataset-builder-rehearsal-20261006'
$py = 'D:\MSE\CAPSTONE\hotel-price-intelligence\eda\.venv\Scripts\python.exe'
Set-Location $ml
$final = "$o\final_${Tag}.log"
function Step($name, $argList) {
    $log = "$o\${name}_${Tag}.log"
    "start $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Encoding utf8
    & $py @argList *>> $log
    $code = $LASTEXITCODE
    "exit=$code end $(Get-Date -Format 'HH:mm:ss')" | Out-File $log -Append -Encoding utf8
    return $code
}
$rc = Step 'clone' @('scripts\clone_warehouse_for_dev.py', '--source', $Source, '--target', $Target, '--drop-existing')
if ($rc -eq 0) { $rc = Step 'init' @('scripts\init_dataset_build.py', '--database', $Target, '--dataset-version', $Version, '--purpose', 'rehearsal', '--anomaly-cutoff', $AnomalyCutoff) }
if ($rc -eq 0) { $rc = Step 'build' @('scripts\build_dataset.py', '--database', $Target, '--dataset-version', $Version, '--apply') }
"exit=$rc end $(Get-Date -Format 'HH:mm:ss')" | Out-File $final -Encoding utf8
