# Windows Task Scheduler への登録スクリプト
# 管理者権限で実行: powershell -ExecutionPolicy Bypass -File install_tasks.ps1

$RepoPath = Split-Path -Parent $PSScriptRoot
$ScriptsPath = Join-Path $RepoPath "scripts"

# logs フォルダを作成
$LogsPath = Join-Path $RepoPath "logs"
if (-not (Test-Path $LogsPath)) {
    New-Item -ItemType Directory -Path $LogsPath | Out-Null
    Write-Host "logs フォルダを作成しました: $LogsPath"
}

# 1. auto-sync (5分ごと git pull)
Write-Host "Task: auto-content-engine-sync を登録..."
$Action1 = New-ScheduledTaskAction -Execute (Join-Path $ScriptsPath "auto_sync.bat")
$Trigger1 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 365)
$Settings1 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "auto-content-engine-sync" -Action $Action1 -Trigger $Trigger1 -Settings $Settings1 -Force

# 2. daily-pipeline (毎日18:00)
Write-Host "Task: auto-content-engine-daily を登録..."
$Action2 = New-ScheduledTaskAction -Execute (Join-Path $ScriptsPath "daily_pipeline.bat")
$Trigger2 = New-ScheduledTaskTrigger -Daily -At "18:00"
$Settings2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "auto-content-engine-daily" -Action $Action2 -Trigger $Trigger2 -Settings $Settings2 -Force

# 3. x-daemon (起動時)
Write-Host "Task: auto-content-engine-x-daemon を登録..."
$Action3 = New-ScheduledTaskAction -Execute (Join-Path $ScriptsPath "x_daemon.bat")
$Trigger3 = New-ScheduledTaskTrigger -AtLogon
$Settings3 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 365)
Register-ScheduledTask -TaskName "auto-content-engine-x-daemon" -Action $Action3 -Trigger $Trigger3 -Settings $Settings3 -Force

# 4. admin-dashboard (起動時、常駐)
Write-Host "Task: auto-content-engine-admin を登録..."
$Action4 = New-ScheduledTaskAction -Execute (Join-Path $ScriptsPath "admin_dashboard.bat")
$Trigger4 = New-ScheduledTaskTrigger -AtLogon
$Settings4 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 365)
Register-ScheduledTask -TaskName "auto-content-engine-admin" -Action $Action4 -Trigger $Trigger4 -Settings $Settings4 -Force

Write-Host ""
Write-Host "✅ 4つのタスクを登録しました"
Write-Host ""
Write-Host "登録済みタスクを確認:"
Get-ScheduledTask -TaskName "auto-content-engine-*" | Format-Table TaskName, State
