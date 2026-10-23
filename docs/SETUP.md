# Configuración

## 1. TikTok Developer Portal

1. Crea una app en el portal de desarrolladores de TikTok.
2. Añade el producto **Content Posting API**.
3. Activa los scopes que necesites:
   - `video.publish`: publicación directa (Direct Post).
   - `video.upload`: sube el video como borrador a la bandeja del usuario.
   - `user.info.basic`: datos básicos de la cuenta.
4. Mientras la app esté en sandbox, añade la cuenta que publicará como usuario de prueba.
5. Para publicar en modo público, TikTok debe auditar la app. Antes de eso solo se admite `SELF_ONLY`.

## 2. Obtener el access token

1. Lanza el flujo OAuth con la cuenta destino pidiendo el scope explícitamente:
   `scope=user.info.basic,video.publish`
2. En la pantalla de consentimiento, acepta todos los permisos. Si desmarcas alguno, el token quedará sin él.
3. Intercambia el `code` por `access_token` y `refresh_token`.
4. Los access tokens caducan. Renueva con el `refresh_token` cuando haga falta.

## 3. Secrets en Databricks

```
databricks secrets create-scope tiktok_pipeline
databricks secrets put-secret tiktok_pipeline apify_token
databricks secrets put-secret tiktok_pipeline tiktok_access_token
```

## 4. Volume de destino

```sql
CREATE VOLUME IF NOT EXISTS tiktok.default.videos;
```

## 5. Importar los notebooks y crear el job

1. Importa `notebooks/*.py` en `/tiktok_pipeline/` del workspace.
2. Crea un job `tiktok_viral_pipeline` con tres tareas encadenadas:
   `scrape_viral_videos` → `download_video` → `upload_to_tiktok`, cada una apuntando a su notebook.

## 6. Cambiar de publicación directa a borrador

Si solo tienes `video.upload`, en `03_upload_to_tiktok.py`:

- Cambia la URL a `https://open.tiktokapis.com/v2/post/publish/inbox/video/init/`.
- Elimina `privacy_level`, `disable_duet`, `disable_comment`, `disable_stitch` y `video_cover_timestamp_ms` de `post_info`.
- El video aparecerá como borrador y habrá que publicarlo desde la app.
