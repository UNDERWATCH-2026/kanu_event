$TaskName = "NespressoMonitor"
$BatFile  = "C:\kanu\run.bat"
$WorkDir  = "C:\kanu"

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$BatFile`"" `
    -WorkingDirectory $WorkDir

$trigger = New-ScheduledTaskTrigger -Daily -At "09:00"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force | Out-Null

Write-Host ""
Write-Host "[OK] Task registered: $TaskName"
Write-Host "     Schedule : daily 09:00"
Write-Host "     Run if missed : YES (StartWhenAvailable)"
Write-Host "     Script : $BatFile"
Write-Host ""
