# Log de publicación en gold

**Estado:** implementado (`registrar_publicacion.py`, commits `80f267e`, `b503d3c`
y `4e1767f`). El notebook no se ha aplicado todavía al job de Databricks.
**Petición del usuario, literal:** «quiero que al finalizar se guarde en una
tabla de gold, una tabla de logs que contenga detalles si se subio el video y
a que red social para tener un tracking».

## El problema que resuelve

Hoy el estado de una publicación está partido en dos sitios y uno de los tres
canales no aparece en ninguno de los dos:

| Fuente | Qué tiene | Qué le falta |
|---|---|---|
| `tiktok.bronze.distribucion_video` | YouTube y Facebook, con `url`, `id_externo` y `motivo_fallo` | **Ninguna fila de TikTok**, nunca |
| `tiktok.bronze.tiktok_uploads` | Solo TikTok, con `status` y `publish_id` | No sabe nada de YouTube ni Facebook |
| `tiktok.gold.guiones_usados` | Todo lo generado, con nombre canónico | No sabe si se publicó |

Consecuencia medida el 2026-10-24: no existe forma de responder «¿este video
salió en las tres redes?» con una consulta. Hay que cruzarlo a mano y el
cruce es frágil porque los nombres no coinciden (`X.mp4` en `guiones_usados`
frente a `X_v2.mp4` en `tiktok_uploads`).

## Decisión: tabla acumulativa, no vista

`tiktok.gold.publicacion_log`. **Una fila por intento**, no por video.

**Por qué acumulativa y no una vista derivada:** una vista siempre está al día
y no hay que mantenerla, pero pierde el historial. El caso que importa aquí es
justamente el reintento. Una vista que solo guarda el último estado borra esa
historia, que es la que explica por qué el sistema se comportó así.

**Alcance real de ese historial (comprobado contra los datos el 2026-10-25).**
Depende de lo que conserve cada fuente, y no es igual en las tres redes:

| Fuente | ¿Conserva intentos? | Qué guarda el log |
|---|---|---|
| `tiktok_uploads` | **Sí**: una fila por envío, con su `uploaded_at` | Una fila por intento |
| `distribucion_video` | **No**: sobrescribe, una fila por (canal, vídeo) | Último estado + `intentos` |

Los 13 reintentos de Facebook por el token caducado **no son recuperables**:
`distribucion_video` ya los sobrescribió, y solo queda el contador `intentos`
con el estado final. El log no puede inventar filas que no existen en el origen.
Lo que sí conserva para YouTube y Facebook es el contador, que dice cuántos
intentos costó llegar a ese estado.

*Coste aceptado:* hay que escribirla y puede quedarse desfasada si el notebook
falla. Se mitiga escribiéndola al final del DAG y con un `MERGE` idempotente,
de modo que relanzar el cierre reconstruye sin duplicar.

## Esquema

| Columna | Tipo | Origen |
|---|---|---|
| `video_name` | string | nombre **canónico** (`nombres.canonico`, sin `_v2`) |
| `canal` | string | `tiktok` \| `youtube` \| `facebook` |
| `estado` | string | vocabulario unificado, ver abajo |
| `estado_origen` | string | el literal de la fuente, sin traducir (`PUBLISH_COMPLETE`, `start_400`…) |
| `id_externo` | string | `publish_id` (TikTok), id de video (YouTube), `video_id` (Facebook) |
| `url` | string | enlace al post cuando la fuente lo tiene |
| `motivo_fallo` | string | el error literal |
| `run_id` | string | corrida que lo intentó (hoy siempre `unknown`) |
| `clave_intento` | string | separa reintentos: el `uploaded_at` en TikTok, `''` en YouTube/Facebook |
| `intento_en` | timestamp | marca de tiempo del intento en la fuente, para ordenarlos |
| `intentos` | int | de `distribucion_video`; 1 para las filas derivadas de TikTok |
| `fuente` | string | `distribucion_video` \| `tiktok_uploads` — de dónde salió el dato |
| `fecha` | date | día de publicación, partición |
| `registrado_en` | timestamp | cuándo escribió el log |

### Vocabulario de `estado`

Se traduce en el notebook, no en las consultas. Así quien lea la tabla no
tiene que conocer el dialecto de cada API.

| `estado` | Viene de | Significa |
|---|---|---|
| `PUBLICADO` | `PUBLICADO` (dist.), `PUBLISH_COMPLETE` (TikTok) | Visible al público |
| `PUBLICADO_PRIVADO` | `PUBLICADO_PRIVADO` | Subido pero privado: YouTube sin auditar |
| `EN_BORRADOR` | `SEND_TO_USER_INBOX` | Aparcado en el inbox de TikTok, falta publicarlo a mano |
| `SALTADO` | `SKIPPED_CUOTA`, `SKIPPED_INBOX_FULL` | No se intentó por cupo |
| `DESCONOCIDO` | cualquier literal no mapeado, sin motivo de fallo | No se sabe qué ocurrió |

Un literal fuera de la tabla (`start_400`, `EN_CURSO`…) se registra como
`FALLIDO` si trae motivo de fallo, y como `DESCONOCIDO` si no. `DESCONOCIDO` no
inventa un éxito; es preferible a adivinar. `estado_origen` conserva siempre el
literal, así que un valor nuevo se puede añadir al vocabulario más adelante sin
haber perdido la información.
| `FALLIDO` | `FALLIDO` | Se intentó y falló |

`estado_origen` conserva el literal para poder depurar sin perder información.

## Cómo se rellena

Un notebook nuevo, `registrar_publicacion`, al final del DAG justo antes de
`cerrar_distribucion`. Tres lecturas y un `MERGE`:

```
distribucion_video  ──┐
                      ├──> normalizar estado ──> MERGE sobre (video_name, canal, run_id, clave_intento)
tiktok_uploads      ──┤
guiones_usados      ──┘   (para saber qué se generó y con qué nombre canónico)
```

- **YouTube y Facebook** salen de `distribucion_video` tal cual.
- **TikTok** se deriva de `tiktok_uploads`, uniendo por nombre normalizado
  contra `guiones_usados` para obtener el nombre canónico.
- El `MERGE` usa la clave `(video_name, canal, run_id, clave_intento)` y
  actualiza el resto, de modo que reejecutar el cierre no duplica.

**Por qué `clave_intento` y no solo `run_id`.** Todo el origen escribe
`run_id = 'unknown'` (verificado: un solo valor distinto en las tres tablas).
Con la clave de tres columnas, los tres reintentos reales de un mismo vídeo de
TikTok colapsaban en una fila y la tabla perdía el historial que la justifica.
En `distribucion_video` la columna vale la cadena vacía a propósito: es
constante, así que el `MERGE` sigue actualizando en vez de insertar. Un `NULL`
no serviría — nunca compara igual consigo mismo en el `ON` y cada reejecución
duplicaría todas las filas de YouTube y Facebook.

## Límites deliberados

- **No toca los notebooks de subida.** El camino crítico de publicación queda
  intacto. Si TikTok empieza a escribir en `distribucion_video` más adelante,
  esta tabla lo recogerá sin cambios, porque la lectura es por canal.
- **Un fallo del log no tumba el DAG.** Va envuelto: la publicación ya
  ocurrió; solo se pierde la fila de registro.
- **Retroactivo en TikTok.** Al derivar de `tiktok_uploads`, el histórico ya
  existente aparece en la primera corrida, incluidos los 33
  `SKIPPED_INBOX_FULL` del 24.
- **No mide alcance.** No guarda likes, vistas ni comentarios: mide si se
  subió y dónde. Las métricas de rendimiento son otra tabla y otro problema.

## Lo que esta tabla hará visible de inmediato

La primera corrida va a mostrar una foto fea que **no es un defecto del log**:
13 fallos de Facebook del run anterior al token permanente y 3 fallos de
YouTube por el límite diario de subida. Son datos reales, y verlos juntos es
exactamente el motivo por el que se pidió esta tabla.
