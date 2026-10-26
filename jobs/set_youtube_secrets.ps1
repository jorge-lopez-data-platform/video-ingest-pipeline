# Guarda las tres credenciales de YouTube en el secret scope `tiktok_pipeline`
# y las verifica con un canje real contra Google.
#
# Existe porque los intentos anteriores dejaron el secreto corrupto: los comandos
# se pegaron con marcas de Slack delante ("[12:26]! databricks ..."), lo que
# desplazo los argumentos y metio la propia linea de comando dentro del valor.
# Google respondia invalid_client y no habia forma de verlo desde fuera.
#
# Ojo: `databricks secrets get-secret` SIEMPRE devuelve {"key": ..., "value": "<base64>"},
# asi que inspeccionar ese JSON no distingue un valor bueno de uno basura.
# El unico test valido es el canje OAuth del final.
#
# Uso:  ! powershell -ExecutionPolicy Bypass -File jobs\set_youtube_secrets.ps1

$ErrorActionPreference = 'Stop'
$Scope   = 'tiktok_pipeline'
$Profile = 'DEFAULT'

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
            Write-Host "  Copia el dato desde la consola de Google, no el JSON." -ForegroundColor Red
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

Write-Host "Configurando credenciales de YouTube en el scope '$Scope'" -ForegroundColor White

$clientId = Leer-Valor `
    "1/3  ID de cliente OAuth" `
    "Google Cloud Console > APIs y servicios > Credenciales" `
    '\.apps\.googleusercontent\.com$' `
    "termina en .apps.googleusercontent.com (NO es el que empieza por GOCSPX-)"

$clientSecret = Leer-Valor `
    "2/3  Secreto de cliente OAuth" `
    "El MISMO cliente OAuth, columna 'Secreto de cliente'" `
    '^GOCSPX-' `
    "empieza por GOCSPX- (este NO termina en .apps.googleusercontent.com)"

$refreshToken = Leer-Valor `
    "3/3  Refresh token" `
    "El que generaste al autorizar la app" `
    '^1//' `
    "empieza por 1//"

Write-Host ""
Write-Host "Guardando en el secret scope..." -ForegroundColor White

# put-secret escribe el valor tal cual; se silencia su salida por limpieza.
$null = databricks secrets put-secret $Scope youtube_client_id     --profile $Profile --string-value $clientId     2>&1
$null = databricks secrets put-secret $Scope youtube_client_secret --profile $Profile --string-value $clientSecret 2>&1
$null = databricks secrets put-secret $Scope youtube_refresh_token --profile $Profile --string-value $refreshToken 2>&1

Write-Host "Guardado. Probando el canje del refresh token contra Google..." -ForegroundColor White

$body = @{
    client_id     = $clientId
    client_secret = $clientSecret
    refresh_token = $refreshToken
    grant_type    = 'refresh_token'
}

try {
    $resp = Invoke-RestMethod -Method Post -Uri 'https://oauth2.googleapis.com/token' `
        -Body $body -ContentType 'application/x-www-form-urlencoded' -TimeoutSec 30
    if (-not $resp.access_token) {
        throw "Google no devolvio access_token."
    }
    Write-Host "OK: Google emitio un access token (expira en $($resp.expires_in) s)." -ForegroundColor Green
    Write-Host "Scope concedido: $($resp.scope)" -ForegroundColor Green
    Write-Host ""
    Write-Host "Listo. Credenciales de YouTube operativas." -ForegroundColor Green
} catch {
    # Invoke-RestMethod no expone el cuerpo del 400 en ErrorDetails: hay que
    # leer el stream de la respuesta, o el mensaje queda en "Solicitud incorrecta".
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
    Write-Host "FALLO el canje: $detalle" -ForegroundColor Red
    Write-Host ""
    if ($detalle -match 'unauthorized_client') {
        # El token existe pero se emitio para otro cliente OAuth. Suele pasar
        # cuando sale del OAuth Playground sin marcar "Use your own OAuth credentials",
        # que entonces emite el token con el cliente propio de Google.
        Write-Host "El refresh token pertenece a OTRO cliente OAuth." -ForegroundColor Yellow
        Write-Host "Genera uno nuevo con el client_id y el client_secret de arriba:" -ForegroundColor Yellow
        Write-Host "  1. https://developers.google.com/oauthplayground" -ForegroundColor Yellow
        Write-Host "  2. Rueda dentada (arriba a la derecha) > marca 'Use your own OAuth credentials'" -ForegroundColor Yellow
        Write-Host "  3. Pega el client_id y el client_secret de ESTE cliente" -ForegroundColor Yellow
        Write-Host "  4. Scope: https://www.googleapis.com/auth/youtube" -ForegroundColor Yellow
        Write-Host "  5. Authorize > Exchange authorization code for tokens > copia el refresh_token" -ForegroundColor Yellow
        Write-Host "  6. Anade https://developers.google.com/oauthplayground como URI de redireccion" -ForegroundColor Yellow
        Write-Host "     en el cliente OAuth de Google Cloud, o el paso 5 fallara." -ForegroundColor Yellow
    } else {
        Write-Host "Revisa que los tres valores sean del MISMO cliente OAuth" -ForegroundColor Yellow
        Write-Host "y que el client_id NO sea el secreto (GOCSPX-)." -ForegroundColor Yellow
    }
    exit 1
}
