# Conseguir un token con `video.publish` y subir tu video (Lomasviral)

Contexto: los intentos anteriores de subir fallaron con `scope_not_authorized`. Eso significa que el
token guardado en `tiktok_pipeline/tiktok_access_token` NO tiene el permiso de publicar (o la app no
tiene habilitado Direct Post). Hay que volver a autorizar y comprobar los permisos concedidos.

Ya está listo: la página de conexión pide los 5 permisos (`user.info.profile, user.info.stats,
video.list, video.upload, video.publish`), el notebook `oauth_tiktok` imprime los permisos concedidos,
y `upload_own_video` sube en modo `SELF_ONLY` desde un Volume.

## 1. Revisar la app en el portal de TikTok
https://developers.tiktok.com/app/7699559281800005639/ (app Lomasviral)

- **Products**: debe estar agregado **Content Posting API** y activado **Direct Post**.
  Si no te deja activarlo sin aprobación (audit), salta a la **alternativa B** de abajo.
- **Scopes**: `video.upload` y `video.publish` deben aparecer en la lista de la app.
- **Sandbox**: la cuenta con la que vas a iniciar sesión debe estar en "Target users" del Sandbox
  (hoy hay un tester llamado "testing"). En Sandbox el client key es el de Sandbox, no el de Producción.

## 2. Autorizar de nuevo (el código es de un solo uso y caduca en minutos)
1. Abre https://mck2775.github.io/lomasviral/ y pulsa **Connect TikTok** (hazlo en el mismo navegador
   hasta el final; la página guarda un `state` de seguridad en esa sesión).
2. Inicia sesión con la cuenta tester y, en la pantalla de permisos, **deja activados todos**,
   incluido el de publicar.
3. La página `callback.html` muestra "Authorization granted for: ..." y el código.
   **Comprueba que la lista incluya `video.publish`.** Copia el código (no lo pegues en chats).

## 3. Cambiar el código por tokens (notebook `oauth_tiktok`)
En Databricks abre `/Users/lopezechagarayjorge@gmail.com/tiktok_ingest/oauth_tiktok` y ejecútalo con:
- `mode` = `exchange`
- `code` = el código copiado
- `redirect_uri` = el valor por defecto (`https://mck2775.github.io/lomasviral/callback.html`)

La salida imprime `Granted scopes:`. **Debe contener `video.publish`.** Si no lo contiene, el permiso
no se concedió (vuelve al paso 1: Direct Post no habilitado) y no tiene sentido intentar subir.
Los tokens se guardan solos en `tiktok_pipeline` y nunca se imprimen. El notebook también imprime
cuánto dura cada token; cuando caduque el de acceso usa `mode=refresh`.

## 4. Subir el video a Databricks (Volume)
```
MSYS_NO_PATHCONV=1 databricks fs cp "C:/Users/lopee/tiktok_videos/no_me_llamen_FINAL_jorge.mp4" "dbfs:/Volumes/tiktok/default/videos/no_me_llamen_jorge.mp4" --profile DEFAULT
```

## 5. Publicar en privado (SELF_ONLY)
Ejecuta el notebook `upload_own_video` con:
- `video_path` = `/Volumes/tiktok/default/videos/no_me_llamen_jorge.mp4`
- `caption` = tu texto (máx. 150 caracteres)
- `privacy_level` = `SELF_ONLY`

Espera `final status: PUBLISH_COMPLETE`. El video queda **solo visible para ti** (una app sin auditar
no puede publicar en público). Para hacerlo público, cámbiale la privacidad desde la app de TikTok.
Como la voz es sintética, **actívale la etiqueta de contenido generado por IA** al editar la publicación.

## Alternativa B: subir como borrador (no exige Direct Post)
Si `video.publish` no se concede, la API de "subida a bandeja" (`/v2/post/publish/inbox/video/init/`,
solo con `video.upload`) deja el video como **borrador** en tu app de TikTok y lo publicas tú desde ahí.
Requiere un notebook casi idéntico a `upload_own_video`; pídelo y se prepara.

## Seguridad
- Códigos de autorización y tokens: nunca en chats, correos ni en el repositorio.
- Si el client secret o algún token se expuso, regenéralo en el portal y actualiza el secret
  `tiktok_pipeline/tiktok_client_secret` antes de continuar.
- Solo contenido propio, solo a tu cuenta.
