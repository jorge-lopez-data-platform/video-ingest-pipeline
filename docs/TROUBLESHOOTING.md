# Solución de problemas

## Apify devuelve 401 o 403

**Dónde:** primera llamada del notebook, al crear el run.

**Causa:** el token del secret `tiktok_pipeline/apify_token` caducó o se revocó.

**Solución:** genera un token nuevo en la consola de Apify y actualízalo:

```bash
databricks secrets put-secret tiktok_pipeline apify_token --profile <PERFIL>
```

Reintentar el job sin cambiarlo no sirve: el token es el mismo.

## `Apify run ... ended with status FAILED`

El mensaje incluye `statusMessage` y `usageTotalUsd`. Las dos causas habituales:

- **Saldo agotado.** El plan gratuito de Apify da 5 USD al mes. Revisa
  `usageTotalUsd` en el log y el consumo en la consola.
- **El actor cambió de esquema.** Comprueba que el campo que buscas
  (`authorMeta.id`, `videoMeta.duration`, …) sigue existiendo con
  `databricks experimental aitools tools get-dataset-items`, o mira un item
  crudo en la run de Apify.

## `TimeoutError ... abort requested`

El run tardó más de `run_timeout_secs` más el margen de 120 s. El notebook pide
`abort` antes de fallar, así que no queda un run cobrando de fondo. Sube el plazo
o reduce `results_per_hashtag`.

## `No valid rows after parsing`

Todas las filas se descartaron por no tener `id` o `author.id`. Casi siempre
significa que el actor devolvió un formato distinto (por ejemplo, un error
envuelto como item). Ejecuta:

```bash
databricks experimental aitools tools query "SELECT * FROM tiktok.bronze.raw_videos LIMIT 5" --profile <PERFIL>
```

y compara las columnas con lo que devuelve el actor hoy.

## `TABLE_OR_SCHEMA_NOT_FOUND` / `AnalysisException: table not found`

Falta el catálogo, alguno de los esquemas o el volumen. El notebook no los crea.
Sigue los pasos 2 y 3 de `docs/SETUP.md`.

## `error decoding JSON ... invalid character '﻿'` al hacer `jobs reset`

El archivo tiene BOM UTF-8. PowerShell 5.1 lo añade con `Out-File -Encoding utf8`.
Reescríbelo sin BOM:

```powershell
$json = Get-Content payload.json -Raw
[System.IO.File]::WriteAllText("payload.json", $json, (New-Object System.Text.UTF8Encoding($false)))
```

Comprueba:

```bash
python -c "print(open('payload.json','rb').read()[:3] == b'\xef\xbb\xbf')"
```

Debe imprimir `False`.

## `INVALID_PARAMETER_VALUE: format` al hacer `jobs reset`

`format` es de solo lectura y se deriva de que el job tenga `tasks`. Elimínalo del
payload junto con `created_time`, `creator_user_name` y `run_as_user_name`.

## El job termina en verde pero las tablas no cambian

Revisa la salida del notebook, no solo el estado del run. Si `bronze_rows` no
sube, el run de Apify devolvió un dataset vacío y el notebook debería haber
fallado con excepción — si no lo hizo, es un bug que hay que corregir.

## Descargar un video con `yt-dlp` devuelve «Unexpected response»

TikTok bloquea clientes sin huella de navegador. El notebook ya usa
`impersonate: chrome` con `curl_cffi`; si falla, verifica que ambas librerías se
instalaron en el entorno del job:

```bash
databricks jobs get-run-output <TASK_RUN_ID> --profile <PERFIL>
```

## Leer el error de un run desde el CLI

```bash
databricks jobs get-run <RUN_ID> --profile <PERFIL> -o json
databricks jobs get-run-output <TASK_RUN_ID> --profile <PERFIL> -o json
```

En Git Bash sobre Windows, antepón `MSYS_NO_PATHCONV=1` a los comandos que usen
rutas del workspace (`/Users/...`), o Git Bash las reescribirá.
