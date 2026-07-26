# Enregistre (ou réenregistre) les tâches planifiées MarketLab.
#   powershell -ExecutionPolicy Bypass -File scripts\planifier.ps1
# Pour tout retirer :
#   Get-ScheduledTask -TaskPath "\MarketLab\" | Unregister-ScheduledTask -Confirm:$false

$scripts = Split-Path -Parent $MyInvocation.MyCommand.Path
$racine = Split-Path -Parent $scripts

if (-not (Test-Path "$racine\.venv\Scripts\python.exe")) {
    Write-Warning "venv absent — les tâches échoueront. Créer d'abord :"
    Write-Warning "  C:\Python314\python.exe -m venv `"$racine\.venv`""
    Write-Warning "  `"$racine\.venv\Scripts\pip.exe`" install -r `"$racine\requirements.txt`""
}

$reglages = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# Alertes : toutes les heures, à partir de la prochaine heure pleine
$declencheurAlertes = New-ScheduledTaskTrigger -Once `
    -At (Get-Date).Date.AddHours((Get-Date).Hour + 1) `
    -RepetitionInterval (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskPath "\MarketLab\" -TaskName "Alertes" `
    -Action (New-ScheduledTaskAction -Execute "$scripts\tache_alertes.cmd") `
    -Trigger $declencheurAlertes -Settings $reglages `
    -Description "MarketLab : signaux forts, RSI extremes, evenements macro -> Telegram" `
    -Force | Out-Null

# Historisation : chaque jour à 22h30 (après clôture US, heure du Bénin)
Register-ScheduledTask -TaskPath "\MarketLab\" -TaskName "Historisation" `
    -Action (New-ScheduledTaskAction -Execute "$scripts\tache_historiser.cmd") `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 22:30) -Settings $reglages `
    -Description "MarketLab : snapshot quotidien des scores du screener" `
    -Force | Out-Null

Get-ScheduledTask -TaskPath "\MarketLab\" | Select-Object TaskName, State
