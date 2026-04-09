# Windows Task Scheduler への登録 v2 — 統合デーモン版
# 旧タスク（daily/x-daemon）を統合デーモン1つに置き換える
# 管理者権限で実行: powershell -ExecutionPolicy Bypass -File install_tasks_v2.ps1

$RepoPath = Split-Path -Parent $PSScriptRoot
$ScriptsPath = Join-Path $RepoPath "scripts"
$LogsPath = Join-Path $RepoPath "logs"
if (-not (Test-Path $LogsPath)) {
    New-Item -ItemType Directory -Path $LogsPath | Out-Null
}

Write-Host "==> 旧タスクを削除..."
Unregister-ScheduledTask -TaskName "auto-content-engine-daily" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "auto-content-engine-x-daemon" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "auto-content-engine-admin" -Confirm:$false -ErrorAction SilentlyContinue

# 1. auto-sync (5分ごと git pull) - 維持
Write-Host "==> auto-sync 登録..."
$Action1 = New-ScheduledTaskAction -Execute (Join-Path $ScriptsPath "auto_sync.bat")
$Trigger1 = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 365)
$Settings1 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "auto-content-engine-sync" -Action $Action1 -Trigger $Trigger1 -Settings $Settings1 -Force | Out-Null

# 2. daemon (起動時に1つだけ常駐 = 全ジョブ管理)
Write-Host "==> daemon 登録..."
$Action2 = New-ScheduledTaskAction -Execute (Join-Path $ScriptsPath "daemon.bat")
$Trigger2 = New-ScheduledTaskTrigger -AtLogon
$Settings2 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 365)
Register-ScheduledTask -TaskName "auto-content-engine-daemon" -Action $Action2 -Trigger $Trigger2 -Settings $Settings2 -Force | Out-Null

# 3. webapp (FastAPI dashboard)
Write-Host "==> webapp 登録..."
$Action3 = New-ScheduledTaskAction -Execute (Join-Path $ScriptsPath "webapp.bat")
$Trigger3 = New-ScheduledTaskTrigger -AtLogon
$Settings3 = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 365)
Register-ScheduledTask -TaskName "auto-content-engine-webapp" -Action $Action3 -Trigger $Trigger3 -Settings $Settings3 -Force | Out-Null

Write-Host ""
Write-Host "[OK] v3タスク構成 (3タスク):"
Write-Host "  - auto-content-engine-sync   (5分ごと git pull)"
Write-Host "  - auto-content-engine-daemon (常駐: 日次パイプライン+X投稿+ジョブキュー)"
Write-Host "  - auto-content-engine-webapp (常駐: FastAPI dashboard :8502)"
Write-Host ""
Get-ScheduledTask -TaskName "auto-content-engine-*" | Format-Table TaskName, State
