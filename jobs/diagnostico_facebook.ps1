# Diagnostico de solo lectura para el token de Facebook.
#
# Lo usa quien ya corrio `canjear_token_facebook.ps1` y recibio
# "no aparece en /me/accounts" con la lista de paginas VACIA. Eso no es un
# problema de id: es que el token no ve ninguna pagina, y hay tres causas
# distintas que desde fuera se ven igual:
#
#   1. Los permisos estan marcados en el Explorer pero no fueron CONCEDIDOS
#      (el consentimiento no se dio despues de marcarlos).
#   2. La app del Explorer no es la misma que la del canje (client_id).
#   3. El usuario no tiene rol de administrador en la pagina.
#
# Este script responde a las tres sin escribir nada en el scope y sin imprimir
# ningun token. Nunca modifica estado: solo hace GET y un debug_token.
#
# Uso, en una ventana de PowerShell normal:
#   powershell -ExecutionPolicy Bypass -File jobs\diagnostico_facebook.ps1

$ErrorActionPreference = 'Stop'
$AppId   = '534115797012427'
$PageId  = '101563571995144'
$Version = 'v26.0'
$Base    = "https://graph.facebook.com/$Version"

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

function Leer-Secreto($titulo, $ayuda) {
    Write-Host ""
    Write-Host $titulo -ForegroundColor Cyan
    Write-Host "  $ayuda" -ForegroundColor DarkGray
    $secure = Read-Host -AsSecureString "  Valor"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try   { return ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

Write-Host "Diagnostico de Facebook (solo lectura, no guarda nada)" -ForegroundColor White

$userToken = Leer-Secreto `
    "1/2  Token de USUARIO (el que acabas de generar)" `
    "Empieza por EAA. No se imprime ni se guarda."

$appSecret = Leer-Secreto `
    "2/2  App Secret (el nuevo)" `
    "32 caracteres hexadecimales. Solo se usa para /debug_token."

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor DarkGray

# --- 1. Quien es el token ---
# /me devuelve el usuario dueno del token. Si esto falla, el token no sirve.
Write-Host "1. Usuario del token" -ForegroundColor White
$me = $null
try {
    $me = Invoke-RestMethod -Method Get -Uri "$Base/me" -Body @{ fields = 'id,name' } `
        -Headers @{ Authorization = "Bearer $userToken" } -TimeoutSec 60
    Write-Host "   OK: $($me.name) ($($me.id))" -ForegroundColor Green
} catch {
    Write-Host "   FALLO: $(Detalle-Error $_)" -ForegroundColor Red
    Write-Host "   El token no es valido. Genera uno nuevo y repite." -ForegroundColor Yellow
    exit 1
}

# --- 2. Permisos realmente CONCEDIDOS ---
# Esta es la comprobacion decisiva. El Explorer deja marcar permisos en la
# lista, pero si el dialogo de consentimiento no se acepto DESPUES de
# marcarlos, el token sale sin ellos y /me/accounts vuelve vacio.
Write-Host ""
Write-Host "2. Permisos concedidos (no solo marcados)" -ForegroundColor White
try {
    $perm = Invoke-RestMethod -Method Get -Uri "$Base/me/permissions" `
        -Headers @{ Authorization = "Bearer $userToken" } -TimeoutSec 60

    $concedidos = @($perm.data | Where-Object { $_.status -eq 'granted' } | ForEach-Object { $_.permission })
    $rechazados = @($perm.data | Where-Object { $_.status -eq 'declined' } | ForEach-Object { $_.permission })

    Write-Host "   Concedidos: $($concedidos -join ', ')" -ForegroundColor Green
    if ($rechazados.Count -gt 0) {
        Write-Host "   RECHAZADOS: $($rechazados -join ', ')" -ForegroundColor Red
    }

    if ($concedidos -notcontains 'pages_show_list') {
        Write-Host ""
        Write-Host "   CAUSA ENCONTRADA: falta pages_show_list." -ForegroundColor Red
        Write-Host "   Marcar la casilla en el Explorer no basta: hay que pulsar" -ForegroundColor Yellow
        Write-Host "   'Generate Access Token' DESPUES de marcarla y aceptar el dialogo." -ForegroundColor Yellow
    }
    if ($concedidos -notcontains 'pages_manage_posts') {
        Write-Host ""
        Write-Host "   AVISO: falta pages_manage_posts; la pagina no podria publicar." -ForegroundColor Red
    }
} catch {
    Write-Host "   FALLO: $(Detalle-Error $_)" -ForegroundColor Red
}

# --- 3. App duena del token ---
# El canje usa client_id = $AppId. Si el token se genero con otra app, el
# canje falla aunque el secreto sea correcto, y el mensaje es enganoso.
Write-Host ""
Write-Host "3. App duena del token" -ForegroundColor White
try {
    # POST con cuerpo: un token en la query string acaba en logs. En el cuerpo no.
    $dbg = Invoke-RestMethod -Method Post -Uri "$Base/debug_token" -Body @{
        input_token  = $userToken
        access_token = "$AppId|$appSecret"
    } -TimeoutSec 60

    $appReal = $dbg.data.app_id
    Write-Host "   App del token: $appReal" -ForegroundColor White
    if ($appReal -eq $AppId) {
        Write-Host "   Coincide con la del canje ($AppId)." -ForegroundColor Green
    } else {
        Write-Host "   NO COINCIDE con la del canje ($AppId)." -ForegroundColor Red
        Write-Host "   El canje fallaria. Genera el token con la app $AppId." -ForegroundColor Yellow
    }
    if ($dbg.data.scopes) {
        Write-Host "   Scopes en el token: $($dbg.data.scopes -join ', ')" -ForegroundColor DarkGray
    }
    if ($dbg.data.expires_at -and $dbg.data.expires_at -ne 0) {
        Write-Host "   El token de usuario caduca en: $(([datetimeoffset]::FromUnixTimeSeconds($dbg.data.expires_at)).ToString('yyyy-MM-dd HH:mm'))" -ForegroundColor DarkGray
    }
} catch {
    Write-Host "   FALLO: $(Detalle-Error $_)" -ForegroundColor Red
    Write-Host "   Suele ser el App Secret equivocado o de otra app." -ForegroundColor Yellow
}

# --- 4. Paginas visibles ---
Write-Host ""
Write-Host "4. Paginas que ve el token (/me/accounts)" -ForegroundColor White
try {
    $cuentas = Invoke-RestMethod -Method Get -Uri "$Base/me/accounts" `
        -Body @{ fields = 'id,name,link,tasks' } `
        -Headers @{ Authorization = "Bearer $userToken" } -TimeoutSec 60

    if (-not $cuentas.data -or @($cuentas.data).Count -eq 0) {
        Write-Host "   NINGUNA pagina." -ForegroundColor Red
        Write-Host ""
        Write-Host "   Posibles causas, en orden de probabilidad:" -ForegroundColor Yellow
        Write-Host "     a) Los permisos estan marcados pero no concedidos (ver bloque 2)." -ForegroundColor Yellow
        Write-Host "     b) Tu usuario no administra la pagina, o su rol no incluye" -ForegroundColor Yellow
        Write-Host "        la tarea CREATE_CONTENT." -ForegroundColor Yellow
        Write-Host "     c) La pagina vive en un portafolio empresarial y falta" -ForegroundColor Yellow
        Write-Host "        business_management en el token." -ForegroundColor Yellow
    } else {
        foreach ($p in $cuentas.data) {
            Write-Host "   - $($p.name) [$($p.id)]" -ForegroundColor White
            Write-Host "     link:  $($p.link)" -ForegroundColor DarkGray
            if ($p.tasks) { Write-Host "     tareas: $($p.tasks -join ', ')" -ForegroundColor DarkGray }
            if ($p.id -eq $PageId) { Write-Host "     <-- es la del script" -ForegroundColor Green }
        }
    }
} catch {
    Write-Host "   FALLO: $(Detalle-Error $_)" -ForegroundColor Red
}

# --- 5. La pagina concreta ---
# Si no sale en /me/accounts pero SI se puede leer por id, existe y el token
# la alcanza: lo que falta entonces es el rol o el scope, no el id.
Write-Host ""
Write-Host "5. La pagina $PageId por id directo" -ForegroundColor White
try {
    $pag = Invoke-RestMethod -Method Get -Uri "$Base/$PageId" -Body @{ fields = 'id,name' } `
        -Headers @{ Authorization = "Bearer $userToken" } -TimeoutSec 60
    Write-Host "   Existe y es visible: $($pag.name)" -ForegroundColor Green
    Write-Host "   El id es correcto; el problema es el rol o el scope." -ForegroundColor Yellow
} catch {
    Write-Host "   No accesible: $(Detalle-Error $_)" -ForegroundColor Yellow
    Write-Host "   Normal si el token no tiene pages_read_engagement concedido." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host ("=" * 60) -ForegroundColor DarkGray
Write-Host "Nada se ha guardado: este script no escribe en el scope." -ForegroundColor DarkGray
Write-Host "Pega en el chat SOLO la salida de arriba: no contiene tokens." -ForegroundColor DarkGray
