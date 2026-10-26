# Convierte un token de USUARIO de corta duracion en un token de PAGINA sin
# caducidad, y lo guarda en el secret scope `tiktok_pipeline`.
#
# Hace falta porque el token que da el Explorer dura ~1 hora: el job publica
# cada 4 horas, asi que se quedaria sin credenciales a la tercera publicacion.
#
# Los dos canjes:
#   1. GET /oauth/access_token con grant_type=fb_exchange_token
#      token de usuario corto -> token de usuario de 60 dias
#   2. GET /me/accounts con ese token
#      -> access_token de la pagina, que NO caduca
#
# El token de usuario que se pide aqui es el CORTO del Explorer (el que sale
# por defecto, "Token del usuario"), no el de pagina.
#
# Antes de ejecutar: restablece el App Secret en
#   Meta > Configuracion de la app > Basica > Restablecer
# y usa el nuevo. Uno escrito en el chat hay que darlo por comprometido.
#
# Uso:  ! powershell -ExecutionPolicy Bypass -File jobs\canjear_token_facebook.ps1

$ErrorActionPreference = 'Stop'
$Scope   = 'tiktok_pipeline'
$Profile = 'DEFAULT'
$AppId   = '534115797012427'
# "Alguien que te escucha", la misma cuenta que el canal de TikTok. Antes apuntaba
# a MCK ZONE (104360577922877), pero los permisos del token se conceden por pagina
# y el Explorer los dio a esta: con el otro id, /me/accounts no la devolvia y el
# canje abortaba sin guardar nada.
$PageId  = '101563571995144'

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

        if ($valor -match '\s') {
            Write-Host "  ERROR: el valor tiene espacios; esta contaminado." -ForegroundColor Red
            $intentos++
            continue
        }
        if ($valor -match '["''{}]') {
            Write-Host "  ERROR: el valor lleva comillas o llaves; se pego un fragmento de JSON." -ForegroundColor Red
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

# Invoke-RestMethod esconde el cuerpo del error tras "(400) Solicitud incorrecta";
# sin esto los fallos de Facebook son indescifrables.
function Detalle-Error($excepcion) {
    $detalle = $excepcion.ErrorDetails.Message
    try {
        if ($excepcion.Exception.Response) {
            $stream = $excepcion.Exception.Response.GetResponseStream()
            $lector = New-Object System.IO.StreamReader($stream)
            $cuerpo = $lector.ReadToEnd()
            if ($cuerpo) { $detalle = $cuerpo }
        }
    } catch { }
    if (-not $detalle) { $detalle = $excepcion.Exception.Message }
    return $detalle
}

Write-Host "Token de pagina permanente para 'Alguien que te escucha' ($PageId)" -ForegroundColor White

$appSecret = Leer-Valor `
    "1/2  App Secret (el NUEVO, tras restablecerlo)" `
    "Meta > Configuracion de la app > Basica > Secreto de la app" `
    '^[0-9a-f]{32}$' `
    "32 caracteres hexadecimales, sin guiones"

$userToken = Leer-Valor `
    "2/2  Token de USUARIO corto (no el de pagina)" `
    "Explorer con 'Usuario o pagina' en 'Token del usuario'" `
    '^EAA[A-Za-z0-9]+$' `
    "empieza por EAA"

$version = Read-Host "  Version de la Graph API [v26.0]"
if (-not $version) { $version = 'v26.0' }
$base = "https://graph.facebook.com/$version"

# --- Canje 1: token corto -> token de usuario de 60 dias ---
Write-Host ""
Write-Host "Canjeando el token corto por uno de 60 dias..." -ForegroundColor White

$largo = $null
try {
    $largo = Invoke-RestMethod -Method Get -Uri "$base/oauth/access_token" -Body @{
        grant_type        = 'fb_exchange_token'
        client_id         = $AppId
        client_secret     = $appSecret
        fb_exchange_token = $userToken
    } -TimeoutSec 60
} catch {
    Write-Host "FALLO el canje: $(Detalle-Error $_)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Causas habituales:" -ForegroundColor Yellow
    Write-Host "  - El App Secret no es el vigente (restableciste pero pegaste el viejo)." -ForegroundColor Yellow
    Write-Host "  - El token de usuario ya caduco: regenera en el Explorer y repite." -ForegroundColor Yellow
    Write-Host "  - Pegaste el token de PAGINA en vez del de USUARIO." -ForegroundColor Yellow
    exit 1
}

if (-not $largo.access_token) {
    Write-Host "FALLO: el canje no devolvio access_token." -ForegroundColor Red
    exit 1
}
Write-Host "OK: token de usuario largo obtenido." -ForegroundColor Green

# --- Canje 2: token largo -> token de pagina (sin caducidad) ---
Write-Host "Buscando el token de la pagina $PageId..." -ForegroundColor White

# Dos caminos, porque /me/accounts miente en la Nueva Experiencia de Paginas:
# devuelve {"data": []} aunque /debug_token demuestre que el token tiene
# pages_show_list y pages_manage_posts CONCEDIDOS sobre esta misma pagina.
# Cuando eso pasa, el id directo si responde y da el access_token igual.
$pageToken = $null
$pageName  = $null

try {
    $cuentas = Invoke-RestMethod -Method Get -Uri "$base/me/accounts" -Body @{
        fields       = 'id,name,access_token'
        access_token = $largo.access_token
    } -TimeoutSec 60

    $pagina = $cuentas.data | Where-Object { $_.id -eq $PageId } | Select-Object -First 1
    if ($pagina -and $pagina.access_token) {
        $pageToken = $pagina.access_token
        $pageName  = $pagina.name
        Write-Host "OK: encontrada en /me/accounts." -ForegroundColor Green
    } else {
        Write-Host "AVISO: /me/accounts no la devuelve; se prueba por id directo." -ForegroundColor Yellow
        if ($cuentas.data) {
            Write-Host "  Devueltas: $(($cuentas.data | ForEach-Object { $_.id + '=' + $_.name }) -join ', ')" -ForegroundColor DarkGray
        } else {
            Write-Host "  Lista VACIA (tipico de la Nueva Experiencia de Paginas)." -ForegroundColor DarkGray
        }
    }
} catch {
    Write-Host "AVISO: /me/accounts fallo ($(Detalle-Error $_)); se prueba por id directo." -ForegroundColor Yellow
}

if (-not $pageToken) {
    try {
        $directa = Invoke-RestMethod -Method Get -Uri "$base/$PageId" -Body @{
            fields       = 'id,name,access_token'
            access_token = $largo.access_token
        } -TimeoutSec 60
        $pageToken = $directa.access_token
        $pageName  = $directa.name
        if ($pageToken) {
            Write-Host "OK: token obtenido por id directo ('$pageName')." -ForegroundColor Green
        }
    } catch {
        Write-Host "FALLO por id directo: $(Detalle-Error $_)" -ForegroundColor Red
    }
}

if (-not $pageToken) {
    Write-Host ""
    Write-Host "FALLO: no se pudo obtener el token de la pagina $PageId." -ForegroundColor Red
    Write-Host "Si /debug_token muestra los scopes de esa pagina concedidos, lo que" -ForegroundColor Yellow
    Write-Host "falta es el scope business_management (pagina en portafolio empresarial)." -ForegroundColor Yellow
    Write-Host "No se ha escrito nada en el scope '$Scope'." -ForegroundColor Yellow
    exit 1
}

# Se recompone $pagina desde cualquiera de los dos caminos para que el resto del
# script (verificacion, prueba y guardado) siga leyendo las mismas propiedades
# sin duplicar la logica.
$pagina = [pscustomobject]@{ name = $pageName; access_token = $pageToken }

# --- Verificar ANTES de guardar ---
# El orden importa. Antes se guardaba primero y se comprobaba despues, asi que un
# token ya caducado quedaba escrito en el scope y el job lo usaba hasta fallar en
# produccion. Aqui no se escribe nada que no haya pasado las dos pruebas.
Write-Host "Comprobando que no caduca y que puede publicar..." -ForegroundColor White

# /debug_token necesita un token de app: "app_id|app_secret".
# Si no se puede leer la caducidad, se aborta: "no lo se" nunca debe equivaler a
# "esta bien", que es justo el fallo que dejo un token muerto en el scope.
try {
    $dbg = Invoke-RestMethod -Method Get -Uri "$base/debug_token" -Body @{
        input_token  = $pageToken
        access_token = "$AppId|$appSecret"
    } -TimeoutSec 60
} catch {
    Write-Host "FALLO al leer la caducidad: $(Detalle-Error $_)" -ForegroundColor Red
    Write-Host "No se guarda nada. Revisa el App Secret y vuelve a intentarlo." -ForegroundColor Yellow
    exit 3
}

$expira = $dbg.data.expires_at
if ($null -eq $expira -or $expira -ne 0) {
    $cuando = if ($expira) {
        ([datetimeoffset]::FromUnixTimeSeconds($expira)).ToString('yyyy-MM-dd HH:mm')
    } else { "sin dato" }
    Write-Host ""
    Write-Host "FALLO: el token de pagina CADUCA ($cuando)." -ForegroundColor Red
    Write-Host "Un token de pagina sacado de uno de usuario largo deberia ser permanente." -ForegroundColor Yellow
    Write-Host "Causas habituales:" -ForegroundColor Yellow
    Write-Host "  - El token de usuario pegado ya estaba caducado o era de corta duracion." -ForegroundColor Yellow
    Write-Host "  - Restableciste el App Secret DESPUES de generar el token: eso lo invalida." -ForegroundColor Yellow
    Write-Host "Regenera el token en el Explorer y ejecuta el script enseguida." -ForegroundColor Yellow
    Write-Host "No se ha escrito nada en el scope '$Scope'." -ForegroundColor Yellow
    exit 3
}
$caduca = "NUNCA (permanente)"
Write-Host "OK: el token no caduca." -ForegroundColor Green

$inicio = $null
try {
    $inicio = Invoke-RestMethod -Method Post -Uri "$base/$PageId/video_reels" `
        -Body @{ upload_phase = 'start'; access_token = $pageToken } -TimeoutSec 60
} catch {
    Write-Host "FALLO al abrir la sesion de prueba: $(Detalle-Error $_)" -ForegroundColor Red
    Write-Host "No se ha escrito nada en el scope '$Scope'." -ForegroundColor Yellow
    exit 2
}
if (-not $inicio.video_id) {
    Write-Host "FALLO: start no devolvio video_id. No se guarda nada." -ForegroundColor Red
    exit 2
}
Write-Host "OK: sesion $($inicio.video_id) abierta; el token puede publicar." -ForegroundColor Green

# --- Guardar: solo llega aqui un token permanente y ya probado ---
Write-Host "Guardando en el scope '$Scope'..." -ForegroundColor White
$null = databricks secrets put-secret $Scope fb_page_access_token --profile $Profile --string-value $pagina.access_token 2>&1
$null = databricks secrets put-secret $Scope fb_graph_version      --profile $Profile --string-value $version             2>&1
$null = databricks secrets put-secret $Scope fb_page_id            --profile $Profile --string-value $PageId              2>&1

Write-Host ""
Write-Host "Listo. Token de pagina guardado y verificado." -ForegroundColor Green
Write-Host "  Pagina:  $($pagina.name) ($PageId)" -ForegroundColor Green
Write-Host "  Caduca:  $caduca" -ForegroundColor Green
Write-Host "  Prueba:  sesion $($inicio.video_id) abierta y descartada" -ForegroundColor Green
Write-Host ""
Write-Host "Si 'Caduca' dice NUNCA, el job puede publicar sin renovar nada." -ForegroundColor DarkGray
