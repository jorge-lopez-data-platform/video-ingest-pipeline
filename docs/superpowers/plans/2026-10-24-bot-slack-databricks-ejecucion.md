# SDD ledger — plan: docs/superpowers/plans/2026-10-24-bot-slack-databricks.md

Spec: docs/superpowers/specs/2026-10-24-bot-slack-databricks-design.md (leído)
Rama: feat/video-propio (no es main; el trabajo del bot es aditivo y no toca pipelines existentes)
Base: c6f47d34d1112861dc9e9d13f21ad48a74d1f307

## Nota de entorno

El árbol de trabajo tiene cambios ajenos sin commitear (scripts/build_video.py,
transcribe.py, README.md, varios `_bak_*`). Todos los dispatches llevan la orden
de hacer `git add` con rutas explícitas, nunca `-A` ni `git add .`.
Bash enruta a WSL, que no está instalado: los scripts `sdd-workspace`,
`task-brief` y `review-package` de la skill se reemplazan a mano con PowerShell.

## Pre-flight scan — pares que comparten fichero o interfaz

| Productor | Consumidor | Interfaz | Resultado |
|---|---|---|---|
| T1 `firma_valida(secret, ts, cuerpo, firma, ahora=None)` | T7 `crear_app` | firma con 5 parámetros | OK, coincide |
| T2 `validar(accion, parametros)` | T4 `construir_lanzamiento` | T4 asume params ya validados | OK — T4 documenta que no revalida |
| T2 `CANALES` | T7 rechazo de Instagram | tupla de 3 canales | OK |
| T3 `canonico(nombre)` | T5 `clasificar_tiktok` | str -> str, tolera None | OK |
| T4 **crea** `slackbot/acciones.py` | T5 **modifica** el mismo fichero | T5 añade funciones | RIESGO: T5 debe añadir, no reescribir. Va en el dispatch. |
| T4 `lanzar(cliente, parametros)` | T7 `deps["lanzar"]` | T7 inyecta `lanzar(params)` — **1 argumento** | **CONFLICTO** → ver Ruling 1 |
| T5 `clasificar_tiktok`, `agrupar_por_canal` | T6 `texto_estado_videos` | dict con `en_inbox`/`pendientes` | OK |
| T6 `tarjeta_confirmacion(accion, parametros, predeterminados, valor_boton)` | T7 | 4 parámetros posicionales | OK |
| T7 `crear_app` define `USADOS` como global de módulo | T7 test de doble clic | set compartido entre tests | **CONFLICTO** → ver Ruling 2 |

## Pre-flight scan — coherencia interna de cada tarea

| Tarea | Comprobación | Resultado |
|---|---|---|
| T1 | tests contra la firma que declara | OK |
| T2 | `cantidad` float/string rechazados; falta el caso `bool` | **HUECO** → ver Ruling 3 |
| T3 | tests contra `canonico` | OK |
| T4 | `job_parameters` como str; solo sobrescribe lo mencionado | OK |
| T5 | `_v2` normalizado en ambos lados | OK |
| T6 | `MAX_LISTA = 15`; aserción corregida a «y 15 más» | OK (corregido antes de commitear) |
| T7 | firma, reintento, Instagram, doble clic | OK salvo Ruling 2 |
| T8 | sin tests; es despliegue y doc | OK |

## Rulings

**Ruling 1** — `deps["lanzar"]` es una función de **un solo argumento**; `main()`
la construye con `functools.partial(lanzar, cliente)`. `crear_app` llama
siempre `deps["lanzar"](parametros)`.
*Por qué:* los tests de T7 no deben tener cliente de Databricks; inyectar la
función ya ligada es lo más simple y mantiene `lanzar(cliente, parametros)`
testeable por separado en T4.
*Coste si me equivoco:* T7 y T4 no encajan y hay que retocar una firma; un test
falla en el acto, no en producción.

**Ruling 2** — El registro de botones usados vive en la instancia de la app
(closure dentro de `crear_app`), no en un global de módulo. Se mantiene
`USADOS` a nivel de módulo solo como alias del conjunto de la app por defecto.
*Por qué:* un global de módulo se filtra entre tests y entre reinicios de app,
y haría que el segundo test de doble clic viera el estado del primero.
*Coste si me equivoco:* un botón legítimo podría rechazarse como «ya ejecutado»
tras reiniciar la app; se detecta en la prueba de humo de T8.

**Ruling 3** — `validar` debe rechazar también `bool` además de `float` y `str`
para `cantidad` y `min_likes` (`isinstance(True, int)` es `True` en Python, así
que `True` pasaría el filtro de entero).
*Por qué:* el LLM puede devolver `true` donde se espera una cifra; aceptarlo
como `1` sería exactamente el «valor inventado» que la spec prohíbe.
*Coste si me equivoco:* una orden con `true` se rechaza con mensaje claro y el
usuario la repite; coste bajo.

---

## Ejecucion

Task 1: implementada (commit 4db26a8), pendiente de revision.

**Ruling 4** — No existe `tests/slackbot/__init__.py`, contra lo que pedía el
dispatch. El implementador demostró que su presencia hace que pytest registre
`tests/slackbot/` como paquete de nivel superior `slackbot`, sombreando el
paquete real y rompiendo `import slackbot.firma` (`tests/` no tiene
`__init__.py`). Se acepta: el nombre de fichero de test es único en el repo.
*Por qué:* un fichero de conveniencia no debe romper la importación que el
resto del plan necesita.
*Coste si me equivoco:* los tests no se colectan; se ve al primer `pytest`.
*Aplicar a Tasks 2-7:* no crear `tests/slackbot/__init__.py` ni `tests/__init__.py`.

Task 1: complete (commits 4db26a8, cd6a6c1) — revisor aprobado; tres notas
menores aplicadas en cd6a6c1 (except ampliado a TypeError/AttributeError,
test del limite exacto de 300 s, comentario autocontenido). 8 tests verdes.
BASE de la Task 2: cd6a6c1.

**Ruling 5** — `slackbot/nombres.py` (T3) duplica la regla de `_v2` que ya vive
en `pendientes_borradores.py:85-88`. No se refactoriza el notebook para que
importe `slackbot/`: se despliega suelto en el workspace y no puede importar
del repo. La duplicacion se acepta como coste de ese limite.
*Por qué:* un notebook autocontenido vale mas que un import que no resuelve.
*Coste si me equivoco:* si la regla cambia hay que tocarla en dos sitios; el
test de T3 fija la semantica, el notebook no se toca en este plan.

**Ruling 6** — `interpretar` NO construye `response_format` ni `temperature=0`;
solo pasa `ESQUEMA` al llamador inyectado, que los arma. El brief se
contradice a si mismo: pide el `response_format` dentro del paso 3 pero fija
la firma `llamar_llm(texto, ESQUEMA)` en las Interfaces, y una funcion de dos
argumentos no puede obligar al LLM sin conocer el cliente.
*Por qué:* la tarea 7 es la unica que sabe con que se habla (OpenRouter) y la
que tiene la clave; meter el transporte aqui romperia la testeabilidad sin
red que justifica la inyeccion.
*Coste si me equivoco:* si el llamador real olvida `temperature=0`, la
respuesta deja de ser determinista; se ve en la prueba de humo de T8.
*Obligacion que crea:* la tarea 7 debe armar
`response_format={"type":"json_schema", ...}` y `temperature=0` a partir de
`ESQUEMA`. Va en su dispatch.

Task 2: complete (commits fdbb09a, f3331a8) — revisor aprobado; hallazgo
importante corregido en f3331a8 (las acciones de consulta ya validan y
filtran parametros; top_n con tope; response_format/temperature anotados
como obligacion de T7). 27 tests verdes en tests/slackbot.
BASE de la Task 3: f3331a8.

Task 3: complete (commit c52ceb1) — revisor aprobado sin hallazgos importantes.
Comprobado fuera del diff: no hay otro `test_nombres.py` ni basenames de test
duplicados en `tests/`, asi que la ausencia de `__init__.py` no causa colision.
BASE de la Task 4: c52ceb1.

**Ruling 7** — El filtro de canales de T4 solo tiene efecto si `lanzar` pasa
`only=[...]` a `run_now`; borrar tareas de `job_parameters` no sirve, porque
`job_parameters` solo lleva parametros, no estructura. Verificado en el
codigo del SDK (`JobsAPI.run_now`, POST `/api/2.2/jobs/run-now`) que el campo
`only: Optional[List[str]]` existe.
*Por qué:* sin `only` una orden «solo TikTok» publicaria tambien en YouTube y
Facebook, que es peor que no filtrar: el usuario cree que esta pidiendo algo
que no pidio.
*Coste si me equivoco:* si `only` no respeta `depends_on`, la corrida fallaria
al lanzar y se ve en el primer intento, no en produccion.
*Aplicar a T7:* `lanzar` es el unico punto que habla con la API.

**Ruling 8** — TikTok pasa a ser filtrable (`TAREAS_CANAL["tiktok"] =
"subir_borradores"`), contra lo que decia el brief («TikTok no es una tarea
desactivable»). Al quitarla se elimina tambien `listar_pendientes`, que solo
compone la cola de esa subida.
*Por qué:* el brief era correcto en el hecho (TikTok no tiene tarea propia)
pero la conclusion era danina: pedir «solo Facebook» habria gastado una de
las 5 subidas de la ventana de 24 h en una red que nadie pidio, y ese cupo no
se recupera. La spec manda «solo lo mencionado».
*Coste si me equivoco:* una orden de solo YouTube/Facebook ya no sube
borradores a TikTok; si el usuario lo esperaba, se ve en el resumen de Slack.
Test que lo fija: `test_tiktok_sin_pedirlo_quita_la_subida`.

Task 4: complete (commits 5bfc12b, 08d71b5) — revisor marco Needs fixes por
el filtro muerto; corregido con `only=` en `run_now`, TikTok filtrable y
`get_run` protegido. 43 tests verdes.
BASE de la Task 5: 08d71b5.

Task 5: complete (commits eb0259f, e2e1deb) — `clasificar_tiktok` y
`agrupar_por_canal` en `acciones.py`, 9 tests en `test_estado_videos.py`.
52 tests verdes.
BASE de la Task 6: e2e1deb.

Hallazgo operativo (no del plan): la corrida 22832872047862 quedo en bucle.
`subir_borradores` hizo 48 iteraciones; la primera paso y las 47 siguientes
fallaron con el mismo 403 determinista `spam_risk_too_many_pending_share`
(inbox de TikTok lleno). El reintento por defecto del job convierte un limite
de cupo en una espera de horas. Cancelarla fue denegado por el clasificador
de permisos; queda para decision del usuario.

Ruling 9: la Task 6 recibe tres resoluciones de ambiguedad — agrupacion de
secciones en tres bloques; los parametros en None se muestran como «por
defecto» con el valor real del job; y `texto_estado_videos` NO ordena ni
reclasifica, solo pinta lo que recibe (el llamador ordena por fecha
descendente, como fija el docstring de `clasificar_tiktok`). Coste si me
equivoco: la tarjeta muestra un texto distinto al que esperaba el plan; el
test del brief es quien manda y ya lo fija.

Ruling 10: la Task 7 recibe en la dispatch lo que su texto no dice — el
llamador del LLM que arma `main()` DEBE construir `response_format` de tipo
json_schema desde `interpretar.ESQUEMA` y `temperature=0` (Ruling 6), y el
registro `USADOS` vive en el cierre de `crear_app`, no como global de modulo
(Ruling 2). Coste si me equivoco: dos instancias de la app compartirian la
memoria de botones, lo que solo importa si se levantan varias.

Task 6: implementacion b701d92 (11 tests en test_slack.py, 63 en
tests/slackbot). Revisor: veredicto A cumple brief; veredicto B Needs fixes
por robustez. Ronda 1 despachada al mismo implementador con cinco hallazgos
(None/NoneKey en `texto_resultado`, «None» literal en filas de canal,
pendiente sin motivo, listas vacias pintadas como dato entendido, claves no
conocidas omitidas en la tarjeta) mas tres tests de frontera del recorte y
dos tests flojos reforzados.

Ruling 11: en la Task 7, la ruta DEBE llamar a `interpretar.validar` sobre lo
que devuelve el `interpretar` inyectado, y responder el texto del ValueError
sin boton. Lo prueban `test_orden_no_valida_no_ofrece_boton` (cantidad "10"
como texto) y `test_instagram_no_se_ejecuta` (que ademas exige que la palabra
«instagram» aparezca en la respuesta, o sea el mensaje del ValueError tal
cual). Sin esto, ningun test de la tarea puede pasar.

Ruling 12: la re-revision marco como «regresion no pedida» que el boton
«Ejecutar» perdiera `style: primary`. Fui yo quien lo mando retirar en la
ronda 1: el brief no lo pide y anadir enfasis visual no es decision del
implementador. No se restaura.

Ruling 13: `texto_resultado` hace `", ".join(tareas_fallidas)`, que
reventaria con un None en la lista. Se deja como esta: `tareas_fallidas` lo
construye la Task 7 desde el estado de las tareas de Databricks, que siempre
son cadenas; blindarlo seria defender una entrada que este codigo genera.
Coste si me equivoco: TypeError al responder a un run con una tarea sin
nombre; visible en el log, no silencioso.

Task 6: complete (commits b701d92, fdbaf7a) — `slackbot/slack.py` con las
tres funciones y `MAX_LISTA`, 19 tests en test_slack.py, 71 en
tests/slackbot. Una ronda de correccion; re-revision aprobo los cinco
hallazgos.
BASE de la Task 7: fdbaf7a.

Task 7: implementacion 9a15434 (config.py, app.py, test_app.py; 79 tests
en tests/slackbot). Estado declarado DONE_WITH_CONCERNS. Concerns: `USADOS`
en el cierre pese a que el brief lo lista como producido; `value` del boton
lleva JSON {id,a,p} sin estado en servidor; `consultar` de `main()` solo
implementa `consultar_estado`; flask instalado en .venv pero requirements es
de la Task 8; modelo por defecto de OpenRouter elegido sin verificar.
Revision de seguridad despachada.

Task 7 revision de seguridad: veredicto A cumple (salvo `USADOS` de modulo,
que se mantiene deliberadamente en el cierre); veredicto B Needs fixes. Sin
bypass de firma y sin fuga de secretos — la propiedad central se sostiene.
Defectos reales: 500 ante cuerpo mal formado (Slack reintenta en bucle),
`X-Slack-Retry-Num` sin descartar en interacciones, `value` no-JSON lanzando
con `{}`, `responder` dentro del candado, `except` desnudo en
`_predeterminados`, silencio si falla `responder`, `KeyError` en
`orden["accion"]`. Mas tests faltantes (firma incorrecta sobre cuerpo
valido, firma ausente en interacciones, cuerpo mal formado) y un test vacuo
(`test_reintento_de_slack_no_lanza_nada`). Ronda 1 despachada.

Juicio del revisor sobre los concerns, aceptado: `consultar` sin las dos
consultas SQL es aceptable en esta tarea pero queda como seguimiento
obligatorio; `strict: False` es necesario porque ESQUEMA no declara todas
las propiedades como required; consumir el id aunque `lanzar` falle es la
eleccion conservadora.

Ruling 14: el implementador cambio el `value` de
`test_boton_ejecutado_dos_veces_solo_lanza_una` de `"uso-unico-1"` a un JSON
`{id,a,p}`. Lo apruebo: conserva la intencion (dos clics sobre el mismo id,
`len(lanzados) == 1`), y el formato de texto plano solo existia en el literal
del test, nunca en una tarjeta real — la linea que construye `valor` no
cambio en el diff. Aceptar un value no-JSON obligaria a lanzar con `{}` sin
validar, que es justo lo que el hallazgo 3 prohibe.

Task 7: complete (commits 9a15434, 5a393da) — `slackbot/config.py`,
`slackbot/app.py`, `test_app.py`. Una ronda de correccion con siete
hallazgos y cinco tests nuevos de seguridad. Re-revision: Approved.
105 tests en tests/slackbot.
BASE de la Task 8: 5a393da.

Seguimiento obligatorio, no resuelto por el plan: `consultar` de `main()`
solo implementa `consultar_estado`. `listar_pendientes` y `estado_videos`
responden «todavia no esta conectada» porque ninguna tarea define su SQL.

Ruling 15: la Task 8 se despacha con alcance reducido a los steps 1, 2, 4 y 6
(requirements.txt, app.yaml, documentacion, commit). Los steps 3 y 5
(`databricks apps create`, los dos `put-secret` y la prueba de humo) son un
efecto externo y un secreto interactivo: los ejecuta el usuario. El
implementador tiene prohibido correr cualquier comando `databricks`.
Coste si me equivoco: la app no queda desplegada al acabar el plan; queda
documentada y lista para que el usuario la despliegue.

Task 8: complete (commit 49a073a) — `slackbot/requirements.txt`,
`slackbot/app.yaml`, seccion «Bot de Slack» en
`docs/operacion-multicanal.md`. Steps 3 y 5 (desplegar app, crear secretos,
prueba de humo) quedan para el usuario por Ruling 15. 105 tests verdes.
Revision final de la rama despachada.

Revision final de la rama: NO lista para desplegar. Verificado contra el job
vivo: `max_pendientes` por defecto es "0", y `pendientes_borradores.py:184`
solo recorta `if MAX_PENDIENTES`, asi que 0 = todos. El bot no puede fijarlo
(no esta en ESQUEMA ni en _PARAMETROS_JOB), de modo que cada lote con TikTok
reproduce el bucle de 48 iteraciones.

Ruling 16: `construir_lanzamiento` fija `max_pendientes` al cupo del inbox
(5) cuando el lote incluye TikTok y el usuario no lo pidio, en vez de heredar
el 0 del job. El spec da por hecho que el job recorta; no lo hace. Coste si
me equivoco: un lote de TikTok sube como mucho 5 borradores por corrida en
lugar de todos — que es el limite real de la API, no una eleccion.

Ruling 17: el empaquetado se corrige moviendo `app.yaml` y
`requirements.txt` a la raiz del repo, con la ruta de codigo de la app
apuntando ahi. Dejarlos dentro de `slackbot/` hace que `python -m
slackbot.app` no resuelva el paquete. Se elimina el bloque `resources:` de
`app.yaml`: no es una clave valida ahi y apuntaba a un warehouse que ningun
codigo usa.

Pendiente de verificar por el usuario, no lo puedo comprobar desde aqui:
si Slack alcanza una Databricks App detras del proxy OAuth del workspace.
Si no, el transporte tiene que ser Socket Mode y eso cambia el spec.

Ruling 18: `app.yaml` y `requirements.txt` viven en la raiz del repo. El
implementador encontro que ya existia un `requirements.txt` con las
dependencias del pipeline local (faster-whisper, av<19, imageio-ffmpeg,
pytest) y fusiono las del bot en el en vez de reemplazarlo. Consecuencia
aceptada y declarada: `databricks apps deploy` desde la raiz instala tambien
esas dependencias pesadas y sube ~130 MB de videos y backups. No lo separo en
un requirements propio porque el paquete `slackbot` tiene que estar en la
raiz de la ruta de codigo para que `python -m slackbot.app` resuelva, y
moverlo habria roto imports en todos los modulos. Coste si me equivoco: el
build de la app tarda mas de lo necesario; no rompe nada.

Correccion final: commit 8611c85. 112 tests verdes en tests/slackbot.

Verificacion final: Approved. Los ocho puntos resueltos, 112 tests verdes.

Task 8 y todas las anteriores: complete. Plan terminado.

Observaciones que sobreviven, aceptadas y no bloqueantes:
- `requirements.txt` de la raiz es el del pipeline: la app instalara tambien
  faster-whisper, av, imageio-ffmpeg y pytest. Puede fallar el build. Hay que
  confirmarlo en un despliegue real o aislar las dependencias del bot.
- El aviso del `except` de `consultar` dice siempre «revisa el identificador
  de la corrida», aunque el fallo sea una caida de Databricks. Texto
  enganoso en ese caso, no un error de comportamiento.
- `cancelar` responde «Cancelado.» aunque el run ya se lanzo, si se pulsa
  despues de Ejecutar. Despista, pero no relanza.
- `max_pendientes` no esta en `ESQUEMA`, asi que la rama «valor explicito»
  del Ruling 16 es inalcanzable desde Slack. Solo se ejercita con test
  directo. No hace dano.
- `responder` y `consultar` del cableado de `main()` no tienen test de
  integracion; solo lo estan `enviar_slack` y las rutas con deps simuladas.

Bloqueantes que NO puedo resolver yo y quedan al usuario:
1. Verificar si Slack alcanza una Databricks App. Todo apunta a que van
   detras del proxy OAuth y no aceptan POST anonimos; si se confirma, el
   transporte tiene que ser Socket Mode y el spec cambia.
2. Desplegar: `apps create`, los dos `put-secret`, `apps deploy`, permisos
   del service principal (READ sobre el scope, CAN_MANAGE_RUN sobre el job).
3. Prueba de humo en un canal de pruebas.
4. Cancelar la corrida 22832872047862, que sigue en bucle.

Seguimiento de codigo no cubierto por el plan:
- La consulta SQL real de `listar_pendientes` y `estado_videos`. Contrato ya
  documentado en `main()`: datos crudos `{generados, subidas, filas}`,
  `subidas` ordenado por fecha descendente, cada fila de `filas` con `canal`.
- La tabla de logs `tiktok.gold.publicacion_log`
  (`docs/superpowers/specs/2026-10-25-log-publicacion-gold.md`), disenada y
  aprobada en forma, sin implementar.

## Transporte de Slack: resuelto por la documentación (2026-10-25)

La incógnita de si Slack puede alcanzar la app Databricks. La respuesta, según
`docs.databricks.com/aws/en/dev-tools/databricks-apps/auth`:

- «both users and service principals authenticate using standard OAuth 2.0
  flows»
- «Databricks prompts them to grant the app permission to act within each
  requested scope»
- «Databricks forwards the user's access token to the app» (`x-forwarded-access-token`)
- El acceso programático se documenta solo como «token authentication».

**Conclusión:** toda petición a una Databricks App pasa por un flujo OAuth 2.0
del workspace. No hay acceso anónimo ni una opción de endpoints públicos, y la
documentación no documenta ninguno. Slack envía POST sin credenciales de
Databricks: el `url_verification` y todos los eventos recibirían un redirect o
un 401 del proxy antes de llegar a Flask.

**Decisión: Socket Mode.** La app abre una conexión WebSocket saliente
autenticada con el app token de Slack. No necesita entrada HTTP, así que el
proxy OAuth deja de importar. Consecuencias:
- `slackbot/app.py` deja de ser un servidor Flask con dos rutas; pasa a un
  consumidor de eventos.
- La verificación de firma HMAC (`slackbot/firma.py`) ya no aplica: en Socket
  Mode la autenticación es el app token. `firma.py` se conserva, pero deja de
  usarse en el camino principal.
- El paquete `slackbot/` y sus 112 tests siguen valiendo: `interpretar`,
  `validar`, `acciones`, `slack` y `nombres` no dependen del transporte.
  Lo que se tira es `config.py` (lectura de secretos por scope sigue valiendo),
  la capa de rutas y `app.yaml`.
- Cualquier reconstrucción tiene que mantener la idempotencia del botón: en
  Socket Mode llegan las interacciones por el mismo socket, y el registro
  `usados` sigue siendo necesario.

**Dónde corre (decidido 2026-10-25):** Databricks App. El proceso abre el
WebSocket saliente desde dentro de la app, así que el proxy OAuth no estorba
porque la app no recibe entrada HTTP. Reutiliza el despliegue ya documentado
y el service principal ya tiene permisos sobre el job y el scope de secretos.
Coste aceptado: si la app se reinicia o se duerme, el socket cae y toca
reconectar; por eso el consumidor debe reconectar con espera creciente.
Plan de ejecución: `docs/superpowers/plans/2026-10-25-bot-slack-socket-mode.md`.
