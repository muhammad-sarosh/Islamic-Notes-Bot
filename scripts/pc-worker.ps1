param([ValidateSet('Start','Stop','Pause','Resume','InstallStartup','RemoveStartup')][string]$Action='Start')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot '.venv-pc\Scripts\python.exe'
$dataPath = Join-Path $projectRoot 'data'
$pausePath = Join-Path $dataPath 'pc-worker.pause'
$taskName = 'IslamicNotesPCReranker'
function Get-WorkerProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        ($_.ExecutablePath -eq $pythonPath -or $_.CommandLine -like "*$pythonPath*") -and
        $_.CommandLine -match 'notes_bot\.pc_(worker|model)'
    }
}
New-Item -ItemType Directory -Force $dataPath | Out-Null
switch ($Action) {
    'Start' {
        if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Run the PC worker installation instructions first.' }
        if (!(Test-Path -LiteralPath (Join-Path $projectRoot '.env.pc'))) { throw 'Create .env.pc first.' }
        if (Get-WorkerProcesses) { Write-Output 'PC worker is already running.'; break }
        Start-Process -FilePath $pythonPath -ArgumentList '-m','notes_bot.pc_worker' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dataPath 'pc-worker.out.log') -RedirectStandardError (Join-Path $dataPath 'pc-worker.log')
    }
    'Stop' {
        # Stop only our listener/model processes launched from this dedicated venv.
        Get-WorkerProcesses | ForEach-Object { Stop-Process -Id $_.ProcessId }
    }
    'Pause' { New-Item -ItemType File -Force $pausePath | Out-Null }
    'Resume' { if (Test-Path -LiteralPath $pausePath) { Remove-Item -LiteralPath $pausePath } }
    'InstallStartup' {
        $taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" Start" -WorkingDirectory $projectRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
        $taskSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Settings $taskSettings -Description 'Start the outbound Islamic Notes BGE worker when signing in.' | Out-Null
    }
    'RemoveStartup' { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
}
