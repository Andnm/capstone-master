# Stop EXACTLY the API + worker started by start_stack.ps1 (reads run\stack_pids.json and compares each PID's start time,
# so a reused PID is never killed). Never touches the user's operational worker. Claude, 2026-09-25.
# ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI and breaks on UTF-8 Vietnamese text.
$pidFile = 'D:\MSE\CAPSTONE\outputs\fullscan-20260924\run\stack_pids.json'
if (-not (Test-Path $pidFile)) { 'no stack recorded'; exit 0 }
$s = Get-Content $pidFile -Raw | ConvertFrom-Json
foreach ($name in 'worker', 'api') {
    $e = $s.$name
    $p = Get-Process -Id $e.pid -ErrorAction SilentlyContinue
    if ($p -and ($p.StartTime.ToString('o') -eq $e.start)) {
        & taskkill.exe /T /F /PID $e.pid | Out-Null
        "stopped $name (pid $($e.pid)) and its child processes"
    } else {
        "$name (pid $($e.pid)) not running (or PID reused) - skipped"
    }
}
Remove-Item $pidFile
Start-Sleep -Seconds 2
$l = Get-NetTCPConnection -LocalPort 8012 -State Listen -ErrorAction SilentlyContinue
"port 8012: " + $(if ($l) { "STILL OPEN (pid $($l.OwningProcess))" } else { 'closed' })
$cd = Get-Process -Name chromedriver -ErrorAction SilentlyContinue
"chromedriver left: " + $(if ($cd) { ($cd | ForEach-Object { $_.Id }) -join ',' } else { 'none' })
