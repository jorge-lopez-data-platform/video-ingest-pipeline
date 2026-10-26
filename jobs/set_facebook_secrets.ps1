# Guarda las tres credenciales de Facebook en el secret scope `tiktok_pipeline`
# y las verifica con una llamada real a la Graph API.
#
# La pagina destino es MCK ZONE (104360577922877), elegida por el usuario entre
# las que administra, porque la marca del canal no tiene pagina propia en Facebook.
#
# Ojo: el token que sirve es el de PAGINA, no el de usuario. El de usuario solo
# devuelve /me/accounts; para llamar a /{page_id}/video_reels hace falta el de
# la pagina, que se obtiene en el Explorer cambiando "Usuario o pagina" a la
# pagina, o con GET /{page_id}?fields=access_token,tasks.
#
# Uso:  ! powershell -ExecutionPolicy Bypass -File jobs\set_facebook_secrets.ps1

$ErrorActionPreference = 'Stop'
$Scope   = 'tiktok_pipeline'
$Profile = 'DEFAULT'
$PageId  = '104360577922877'

function Leer-Valor($titulo, $ayuda, $patron, $descripcionPatron) {
    Write-Host ""
    Write-Host $titulo -ForegroundColor Cyan
    Write-Host "  Formato esperado: $descripcionPatron" -ForegroundColor DarkGray
    Write-Host "  $ayuda" -ForegroundColor DarkGray

    $intentos = 0
    while ($intentos -lt 5) {
        $secure = Read-Host -AsSecureString "  Pega el valor y pulsa Enter"
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        try   { $valor = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
        $valor = $valor.Trim()

        if ($valor.TrimStart().StartsWith('{')) {
            Write-Host "  ERROR: eso es la SALIDA de un comando, no el valor." -ForegroundColor Red
            Write-Host "  Copia solo el texto entre comillas, no el JSON entero." -ForegroundColor Red
            $intentos++
            continue
        }
        if ($valor -match '\s') {
            Write-Host "  ERROR: el valor tiene espacios; esta contaminado con texto de otro comando." -ForegroundColor Red
            $intentos++
            continue
        }
        if ($valor -match '["''{}]') {
            Write-Host "  ERROR: el valor lleva comillas o llaves; se pego un fragmento de JSON." -ForegroundColor Red
            Write-Host "  Copia solo el token, sin las comillas ni las llaves alrededor." -ForegroundColor Red
            $intentos++
            continue
        }
        if ($valor -notmatch $patron) {
            Write-Host "  ERROR: no tiene la forma esperada." -ForegroundColor Red
            $intentos++
            continue
        }
        Write-Host "  OK (longitud $($valor.Length))" -ForegroundColor Green
        return $valor
    }
    throw "Demasiados intentos fallidos en '$titulo'."
}

Write-Host "Configurando credenciales de Facebook en el scope '$Scope'" -ForegroundColor White
Write-Host "Pagina destino: MCK ZONE ($PageId)" -ForegroundColor White

# El token de pagina y el de usuario se distinguen por la verificacion del final:
# /{page_id} solo responde con el de pagina.
$pageToken = Leer-Valor `
    "1/2  Token de PAGINA (no el de usuario)" `
    "Explorer > 'Usuario o pagina' > elige MCK ZONE" `
    '^EAA[A-Za-z0-9]+$' `
    "empieza por EAA y es largo; NO es el 'App Secret' que empieza por e599"

$graphVersion = Leer-Valor `
    "2/2  Version de la Graph API" `
    "La que muestra el Explorer arriba a la derecha, por ejemplo v26.0" `
    '^v\d+\.\d+$' `
    "v seguido de la version, por ejemplo v26.0"

Write-Host ""
Write-Host "Guardando en el secret scope..." -ForegroundColor White

$null = databricks secrets put-secret $Scope fb_page_id           --profile $Profile --string-value $PageId       2>&1
$null = databricks secrets put-secret $Scope fb_page_access_token --profile $Profile --string-value $pageToken    2>&1
$null = databricks secrets put-secret $Scope fb_graph_version     --profile $Profile --string-value $graphVersion 2>&1

Write-Host "Guardado. Comprobando el token contra la Graph API..." -ForegroundColor White

# `tasks` NO es legible en el nodo de pagina: la API responde
# (#100) Tried accessing nonexisting field (tasks). Solo se expone en
# /me/accounts con token de USUARIO. Aqui basta con id y name.
$uri = "https://graph.facebook.com/$graphVersion/$PageId" + "?fields=id,name"
try {
    $resp = Invoke-RestMethod -Method Get -Uri $uri `
        -Headers @{ Authorization = "Bearer $pageToken" } -TimeoutSec 30
} catch {
    # Igual que en el script de YouTube: el cuerpo del error viene en el stream,
    # no en ErrorDetails, y sin el el mensaje queda en "(400) Solicitud incorrecta".
    $detalle = $_.ErrorDetails.Message
    try {
        if ($_.Exception.Response) {
            $stream = $_.Exception.Response.GetResponseStream()
            $lector = New-Object System.IO.StreamReader($stream)
            $cuerpoError = $lector.ReadToEnd()
            if ($cuerpoError) { $detalle = $cuerpoError }
        }
    } catch { }
    if (-not $detalle) { $detalle = $_.Exception.Message }
    Write-Host "FALLO la comprobacion: $detalle" -ForegroundColor Red
    exit 1
}

if ($resp.id -ne $PageId) {
    Write-Host "FALLO: la API devolvio la pagina $($resp.id), no $PageId." -ForegroundColor Red
    exit 1
}

Write-Host "OK: token valido para '$($resp.name)' (id $($resp.id))." -ForegroundColor Green

# Leer la pagina solo prueba que el token es SUYO. El permiso de escritura se
# comprueba abriendo una sesion de reel (upload_phase=start). No se termina, asi
# que no se publica nada: Facebook descarta la sesion sola y devuelve el
# video_id igual, que es justo lo que hace falta saber.
Write-Host "Comprobando el permiso de publicacion (sesion start sin terminar)..." -ForegroundColor White

$inicio = $null
try {
    $inicio = Invoke-RestMethod -Method Post `
        -Uri "https://graph.facebook.com/$graphVersion/$PageId/video_reels" `
        -Headers @{ Authorization = "Bearer $pageToken" } `
        -Body @{ upload_phase = 'start' } -TimeoutSec 60
} catch {
    $detalle = $_.ErrorDetails.Message
    try {
        if ($_.Exception.Response) {
            $stream = $_.Exception.Response.GetResponseStream()
            $lector = New-Object System.IO.StreamReader($stream)
            $cuerpoError = $lector.ReadToEnd()
            if ($cuerpoError) { $detalle = $cuerpoError }
        }
    } catch { }
    if (-not $detalle) { $detalle = $_.Exception.Message }
    Write-Host "FALLO al abrir la sesion: $detalle" -ForegroundColor Red
    Write-Host ""
    Write-Host "El token lee la pagina pero no puede publicar." -ForegroundColor Yellow
    Write-Host "Anade el permiso pages_manage_posts en el Explorer, pulsa" -ForegroundColor Yellow
    Write-Host "'Generate Access Token', elige la pagina en 'Usuario o pagina'" -ForegroundColor Yellow
    Write-Host "y vuelve a ejecutar este script con el token nuevo." -ForegroundColor Yellow
    exit 2
}

if (-not $inicio.video_id) {
    Write-Host "FALLO: start no devolvio video_id: $($inicio | ConvertTo-Json -Compress)" -ForegroundColor Red
    exit 2
}

Write-Host "OK: sesion abierta (video_id $($inicio.video_id))." -ForegroundColor Green
Write-Host ""
Write-Host "Listo. Credenciales de Facebook operativas y con permiso de publicacion." -ForegroundColor Green
Write-Host "La sesion de prueba se descarta sola; no se ha publicado nada." -ForegroundColor DarkGray
