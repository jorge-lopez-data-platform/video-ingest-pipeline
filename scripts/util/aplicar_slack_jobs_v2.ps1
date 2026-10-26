# Aplica webhook_notifications (Slack) a los 6 jobs de TikTok.
#
# POR QUÉ API 2.2 Y NO `databricks jobs reset`:
# el comando de la CLI usa POST /api/2.1/jobs/reset internamente, y esa version
# NO conoce `webhook_notifications`: el campo se acepta sin error y luego se
# devuelve vacio ({}). Verificado 2026-10-24 con GET directo a 2.1 y a 2.2:
# ambos leian {} tras un reset por CLI. La 2.2 si lo persiste.
#
# Uso:  powershell -ExecutionPolicy Bypass -File aplicar_slack_jobs_v2.ps1
#
# Reversible: los backups previos estan en backup_jobs_20261024\job_<id>.json

$ErrorActionPreference = "Stop"

$DestinationId = "477cffb7-1b7c-4893-b0ca-ab7bbaf989d8"  # destination "TikTok Alerts"
$Profile       = "DEFAULT"
$Host_         = "https://dbc-e83fc5f3-f80b.cloud.databricks.com"
$BackupDir     = Join-Path $PSScriptRoot "backup_jobs_20261024"

$JobIds = @(
    813880207704409,  # tiktok_guion_video_draft   (fuente de verdad: job_reset.json)
    294643041593670,  # tiktok_jesucitos_draft
    646167974890545,  # tiktok_guiones_60s_viral
    349008211136613,  # tiktok_download_own_video
    259180851702003,  # tiktok_viral_pipeline
    584295967848266   # tiktok_ingest_daily
)

if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir | Out-Null }

$token = (databricks auth token --profile $Profile -o json | ConvertFrom-Json).access_token
$headers = @{ Authorization = "Bearer $token" }

foreach ($JobId in $JobIds) {
    Write-Host "=== Job $JobId"

    $settings = (Invoke-RestMethod -Uri "$Host_/api/2.2/jobs/get?job_id=$JobId" `
                                   -Headers $headers).settings

    # Campos de solo lectura: reset los rechaza. `format` se deriva de `tasks`.
    foreach ($ro in @("format", "created_time", "creator_user_name", "run_as_user_name",
                      "effective_budget_policy_id", "budget_policy_id")) {
        if ($settings.PSObject.Properties.Name -contains $ro) {
            $settings.PSObject.Properties.Remove($ro)
        }
    }

    # Respaldo (sin BOM, que rompe algunos parsers).
    [System.IO.File]::WriteAllText(
        (Join-Path $BackupDir "job_$JobId.before_slack.json"),
        ($settings | ConvertTo-Json -Depth 40),
        (New-Object System.Text.UTF8Encoding($false)))

    # Nivel job: solo duracion. on_failure a nivel job duplicaria lo que ya
    # avisa cada tarea.
    $settings | Add-Member -NotePropertyName "webhook_notifications" -Force `
        -NotePropertyValue ([ordered]@{
            on_duration_warning_threshold_exceeded = @([ordered]@{ id = $DestinationId })
        })

    # Nivel tarea: inicio, exito y fallo, para seguir el flujo en vivo.
    foreach ($task in $settings.tasks) {
        $task | Add-Member -NotePropertyName "webhook_notifications" -Force `
            -NotePropertyValue ([ordered]@{
                on_start   = @([ordered]@{ id = $DestinationId })
                on_success = @([ordered]@{ id = $DestinationId })
                on_failure = @([ordered]@{ id = $DestinationId })
            })
    }

    $body = @{ job_id = $JobId; new_settings = $settings } | ConvertTo-Json -Depth 40

    try {
        Invoke-RestMethod -Uri "$Host_/api/2.2/jobs/reset" -Method Post -Headers $headers `
                          -ContentType "application/json" -Body $body | Out-Null
    } catch {
        Write-Warning "  FALLO en reset: $($_.Exception.Message)"
        continue
    }

    # Verificar que de verdad quedo guardado; el modo de fallo actual es
    # "acepta y descarta", asi que no basta con que no lance excepcion.
    $check = (Invoke-RestMethod -Uri "$Host_/api/2.2/jobs/get?job_id=$JobId" -Headers $headers).settings
    $tasksOk = @($check.tasks | Where-Object { $_.webhook_notifications.on_success }).Count
    if ($check.webhook_notifications.on_duration_warning_threshold_exceeded -and
        $tasksOk -eq $check.tasks.Count) {
        Write-Host "  OK: job + $tasksOk/$($check.tasks.Count) tareas notifican a Slack"
    } else {
        Write-Warning "  NO PERSISTIDO: job=$($check.webhook_notifications | ConvertTo-Json -Compress) tareas_ok=$tasksOk"
    }
}

Write-Host ""
Write-Host "Siguiente paso: sincronizar job_reset.json (fuente de verdad de"
Write-Host "813880207704409) con el bloque webhook_notifications que acaba de aplicarse,"
Write-Host "o un futuro 'jobs reset' desde ese archivo lo borrara."
