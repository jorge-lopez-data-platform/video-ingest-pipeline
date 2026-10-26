# Añade webhook_notifications (Slack) a los jobs de TikTok en Databricks.
#
# Por qué un script y no ediciones a mano: los jobs viven en el workspace, no en
# un bundle, y `jobs reset` necesita la definición completa. Esto exporta la
# actual, le inyecta el bloque de notificaciones y la vuelve a aplicar, de modo
# que no se pierde ningún campo existente.
#
# Uso:  powershell -ExecutionPolicy Bypass -File aplicar_slack_jobs.ps1

$ErrorActionPreference = "Stop"

$DestinationId = "477cffb7-1b7c-4893-b0ca-ab7bbaf989d8"  # "TikTok Alerts" en Databricks
$Profile       = "DEFAULT"
$BackupDir     = Join-Path $PSScriptRoot "backup_jobs_20261024"

# Marca de tiempo unica por ejecucion: sin ella, correr el script dos veces
# sobrescribe los respaldos buenos (los del estado previo a los webhooks).
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"

# 813880207704409 se aplica aparte con job_reset_slack.json (tiene fuente de
# verdad propia); estos son el resto.
$JobIds = @(
    294643041593670,  # tiktok_jesucitos_draft
    646167974890545,  # tiktok_guiones_60s_viral
    349008211136613,  # tiktok_download_own_video
    259180851702003,  # tiktok_viral_pipeline
    584295967848266   # tiktok_ingest_daily
)

if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir | Out-Null }

function New-WebhookBlock {
    # Devuelve un OrderedDictionary nuevo: reutilizar el mismo objeto en varios
    # sitios haría que ConvertTo-Json lo serializara mal por referencia.
    $b = [ordered]@{}
    $b["on_start"]   = @([ordered]@{ id = $DestinationId })
    $b["on_success"] = @([ordered]@{ id = $DestinationId })
    $b["on_failure"] = @([ordered]@{ id = $DestinationId })
    return $b
}

foreach ($JobId in $JobIds) {
    Write-Host "=== Job $JobId"

    $json = databricks jobs get $JobId --profile $Profile -o json 2>$null
    if (-not $json) { Write-Warning "  no se pudo leer el job $JobId; se omite"; continue }

    $settings = ($json | ConvertFrom-Json).settings

    # `jobs get` devuelve campos de solo lectura que `jobs reset` rechaza; el mas
    # importante es `format` ("MULTI_TASK"), que se deriva de que haya `tasks`.
    # Sin quitarlo, el reset falla (verificado 2026-10-24).
    foreach ($ro in @("format", "created_time", "creator_user_name", "run_as_user_name")) {
        if ($settings.PSObject.Properties.Name -contains $ro) {
            $settings.PSObject.Properties.Remove($ro)
        }
    }

    # Respaldo antes de tocar nada. WriteAllText evita el BOM que mete
    # `Out-File -Encoding utf8` en PowerShell 5.1, y que rompe `--json @ruta`.
    [System.IO.File]::WriteAllText(
        (Join-Path $BackupDir "job_${JobId}_$Stamp.json"),
        ($settings | ConvertTo-Json -Depth 30),
        (New-Object System.Text.UTF8Encoding($false)))

    # Nivel job: solo duración. on_failure a nivel job duplicaría los avisos que
    # ya manda cada tarea.
    $settings | Add-Member -NotePropertyName "webhook_notifications" `
                           -NotePropertyValue ([ordered]@{
                               on_duration_warning_threshold_exceeded = @([ordered]@{ id = $DestinationId })
                           }) -Force

    # Nivel tarea: inicio, éxito y fallo, para seguir el flujo en vivo.
    foreach ($task in $settings.tasks) {
        $task | Add-Member -NotePropertyName "webhook_notifications" `
                           -NotePropertyValue (New-WebhookBlock) -Force
    }

    $payload = [ordered]@{ job_id = $JobId; new_settings = $settings } |
        ConvertTo-Json -Depth 30 -Compress

    $tmp = Join-Path $env:TEMP "job_slack_$JobId.json"
    [System.IO.File]::WriteAllText(
        $tmp, $payload, (New-Object System.Text.UTF8Encoding($false)))

    $out = databricks jobs reset --profile $Profile --json "@$tmp" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK: notificaciones aplicadas ($($settings.tasks.Count) tareas)"
        # Solo se descarta el payload cuando el reset funcionó; si falla se
        # conserva para poder inspeccionar exactamente qué se envió.
        Remove-Item $tmp -ErrorAction SilentlyContinue
    } else {
        Write-Warning "  FALLO: $out"
        Write-Warning "  payload conservado en: $tmp"
    }
}

Write-Host ""
Write-Host "Recuerda aplicar el job principal aparte:"
Write-Host "  cd C:\Users\lopee\tiktok-pipeline"
Write-Host "  databricks jobs reset --profile DEFAULT --json @job_reset_slack.json"
