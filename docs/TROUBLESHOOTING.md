# Solución de problemas

## `AssertionError: Init falló: ... 'scope_not_authorized'`

**Dónde:** tarea `upload_to_tiktok`, en la llamada a `/v2/post/publish/video/init/`.

**Causa:** el token del secret `tiktok_pipeline/tiktok_access_token` no tiene el scope `video.publish`. Reintentar el job no cambia nada, porque el token sigue siendo el mismo.

**Solución:**
1. Comprueba en el Developer Portal que la app tiene Content Posting API y Direct Post.
2. Reautoriza la cuenta con `scope=user.info.basic,video.publish` y acepta todos los permisos.
3. Actualiza el secret: `databricks secrets put-secret tiktok_pipeline tiktok_access_token`.
4. Relanza el job.

Alternativa: usar el flujo de borrador con `video.upload`. Ver [SETUP.md](SETUP.md), sección 6.

## Qué endpoint requiere cada scope

| Endpoint | Scope |
|---|---|
| `/v2/post/publish/video/init/` | `video.publish` |
| `/v2/post/publish/inbox/video/init/` | `video.upload` |

## `unaudited_client_can_only_post_to_private_accounts`

La app no ha pasado la auditoría de TikTok. Solo se puede publicar con `privacy_level: SELF_ONLY`.

## `Sin URL de descarga` en el notebook 01

El actor de Apify no devolvió `mediaUrls` ni `videoMeta.downloadAddr`. Revisa que `shouldDownloadVideos` esté en `true` y los campos que devuelve la versión actual del actor.

## Leer el error de un run desde el CLI

```
databricks jobs get-run <RUN_ID> -o json
databricks jobs get-run-output <TASK_RUN_ID> -o json
```

En Git Bash en Windows, antepón `MSYS_NO_PATHCONV=1` a los comandos que usen rutas del workspace (`/tiktok_pipeline/...`), o Git Bash las reescribe.

## Descargar un video con yt-dlp devuelve "Unexpected response"

TikTok bloquea clientes sin huella de navegador. Instala `curl_cffi` y usa `--impersonate chrome`.
