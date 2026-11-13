# Windows: set up .venv and start server.py now and at every login (scheduled task "RemoteScreen", runs in your
# desktop session, restarts if it stops). .\install.ps1 -Remove undoes it.
param([switch]$Remove)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Task = "RemoteScreen"
$Me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false
}
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like "*$PSScriptRoot*server.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
if ($Remove) { "Removed."; return }

if (-not (Test-Path .venv\Scripts\python.exe)) { py -3 -m venv .venv; if (-not $?) { python -m venv .venv } }
.venv\Scripts\python.exe -m pip install -q --upgrade pip
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
.venv\Scripts\python.exe server.py --url | Out-Null   # makes config.json + the key on first run

$action = New-ScheduledTaskAction -Execute "$PSScriptRoot\.venv\Scripts\pythonw.exe" -Argument "`"$PSScriptRoot\server.py`"" -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $Me
$principal = New-ScheduledTaskPrincipal -UserId $Me -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Start-ScheduledTask -TaskName $Task
"Running. Viewer: " + (.venv\Scripts\python.exe server.py --url)
"If Listen says there's no microphone: Settings > Privacy > Microphone > let desktop apps use it."
