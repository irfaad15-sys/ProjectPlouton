# Plouton watchdog — relaunches the backend bot and frontend dashboard if either
# is not responding. Run by Task Scheduler at logon and every 10 minutes.
$repo = $PSScriptRoot
Set-Location $repo

function Test-Up($url) {
    try { Invoke-WebRequest -Uri $url -TimeoutSec 5 -UseBasicParsing | Out-Null; return $true }
    catch { return $false }
}

# Backend (bot + API on 8090) — the critical piece
if (-not (Test-Up 'http://localhost:8090/api/bot_state')) {
    $py = 'C:\Users\irfaa\AppData\Local\Python\pythoncore-3.14-64\python.exe'
    if (-not (Test-Path $py)) { $py = 'python' }
    $env:STRATEGY_NAME = 'fibgz'
    Start-Process -FilePath $py -ArgumentList 'dev_runner.py' -WorkingDirectory $repo -WindowStyle Minimized
}

# Frontend (Vite dashboard on 5173) — best effort
if (-not (Test-Up 'http://localhost:5173')) {
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','npm run dev' `
        -WorkingDirectory (Join-Path $repo 'frontend') -WindowStyle Minimized
}
