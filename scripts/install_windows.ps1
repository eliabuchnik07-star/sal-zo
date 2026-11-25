# Sal Zol: installs the hourly price download for chains that block servers abroad.
# Run in PowerShell:  irm https://raw.githubusercontent.com/eliabuchnik07-star/sal-zo/main/scripts/install_windows.ps1 | iex
$ErrorActionPreference = "Stop"
$dir = Join-Path $env:LOCALAPPDATA "sal-zo"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
foreach ($f in "fetch_prices.py", "local_fetch.py") {
  Invoke-WebRequest "https://raw.githubusercontent.com/eliabuchnik07-star/sal-zo/main/scripts/$f" -OutFile (Join-Path $dir $f) -UseBasicParsing
}
$py = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $py) { Write-Host "Python is not installed. Install it from python.org (tick 'Add Python to PATH') and run this again."; return }
if (-not [Environment]::GetEnvironmentVariable("SAL_ZO_TOKEN", "User")) {
  $sec = Read-Host "Paste the GitHub token (it will not be shown)" -AsSecureString
  $tok = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  [Environment]::SetEnvironmentVariable("SAL_ZO_TOKEN", $tok, "User")
  $env:SAL_ZO_TOKEN = $tok
}
# refresh the scripts from GitHub before every run, then run
$cmd = "-NoProfile -WindowStyle Hidden -Command `"cd '$dir'; foreach(`$f in 'fetch_prices.py','local_fetch.py'){try{iwr https://raw.githubusercontent.com/eliabuchnik07-star/sal-zo/main/scripts/`$f -OutFile `$f -UseBasicParsing}catch{}}; & '$py' local_fetch.py *> last_run.txt`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $cmd
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 50)
Register-ScheduledTask -TaskName "Sal Zol prices" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Installed. First run in about a minute. Log: $dir\last_run.txt"
