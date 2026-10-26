# Deriva el token de PAGINA a partir del token de USUARIO de larga duracion y
# lo guarda en el secret scope `tiktok_pipeline`.
#
# Se usa cuando ya se ha hecho el canje fb_exchange_token (el token de usuario
# ya dura 60 dias) pero falta el paso final: /me/accounts devuelve el token de
# la pagina, que es el unico que sirve para /{page_id}/video_reels.
#
# No pide el App Secret: /me/accounts funciona con el token de usuario solo.
#
# Uso: ejecutar en una ventana de PowerShell normal (no desde Claude Code, que
# manda a segundo plano los comandos que tardan en pedir entrada).
#   powershell -ExecutionPolicy Bypass -File jobs\guardar_token_pagina.ps1

$ErrorActionPreference = 'Stop'
$Scope   = 'tiktok_pipeline'
$Profile = 'DEFAULT'
# "Alguien que te escucha", la que tiene los permisos concedidos en el token y la
# misma marca que el canal de TikTok. Antes apuntaba a MCK ZONE (104360577922877),
# que no aparece en /me/accounts con este token.
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

Write-Host "Token de pagina para 'Alguien que te escucha' ($PageId)" -ForegroundColor White
Write-Host ""
Write-Host "Pega el token de USUARIO de larga duracion (el que caduca el 23-dic-2026)." -ForegroundColor Cyan
Write-Host "  Empieza por EAA. NO es el App Secret." -ForegroundColor DarkGray

$secure = Read-Host -AsSecureString "  Token"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try   { $userToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
$userToken = $userToken.Trim()

if ($userToken -notmatch '^EAA[A-Za-z0-9]+$') {
    Write-Host "ERROR: no parece un token (debe empezar por EAA y no tener espacios)." -ForegroundColor Red
    exit 1
}
Write-Host "  Formato OK (longitud $($userToken.Length))." -ForegroundColor Green

Write-Host ""
Write-Host "Buscando el token de la pagina..." -ForegroundColor White

try {
    $cuentas = Invoke-RestMethod -Method Get -Uri "$Base/me/accounts" -Body @{
        fields       = 'id,name,access_token'
        access_token = $userToken
    } -TimeoutSec 60
} catch {
    Write-Host "FALLO: $(Detalle-Error $_)" -ForegroundColor Red
    exit 1
}

$pagina = $cuentas.data | Where-Object { $_.id -eq $PageId } | Select-Object -First 1
if (-not $pagina) {
    Write-Host "FALLO: la pagina $PageId no aparece." -ForegroundColor Red
    if (-not $cuentas.data) {
        # Una lista vacia no es "no coincide el id": es que el token no administra
        # ninguna pagina, asi que no hay ningun id que buscar. Sin este aviso el
        # mensaje "Devueltas:" sale en blanco y parece un fallo de comparacion.
        Write-Host "El token no devolvio NINGUNA pagina: no administra ninguna con estos permisos." -ForegroundColor Yellow
        Write-Host "Revisa que el token sea el de USUARIO recien generado, no uno de pagina." -ForegroundColor Yellow
    } else {
        Write-Host "Devueltas: $(($cuentas.data | ForEach-Object { $_.id + '=' + $_.name }) -join ', ')" -ForegroundColor Yellow
    }
    exit 1
}
if (-not $pagina.access_token) {
    Write-Host "FALLO: '$($pagina.name)' vino sin access_token." -ForegroundColor Red
    Write-Host "Falta pages_manage_posts en el token de usuario." -ForegroundColor Yellow
    exit 1
}
Write-Host "OK: token obtenido para '$($pagina.name)'." -ForegroundColor Green

Write-Host "Guardando en el scope '$Scope'..." -ForegroundColor White
$null = databricks secrets put-secret $Scope fb_page_access_token --profile $Profile --string-value $pagina.access_token 2>&1
$null = databricks secrets put-secret $Scope fb_page_id           --profile $Profile --string-value $PageId              2>&1
$null = databricks secrets put-secret $Scope fb_graph_version     --profile $Profile --string-value $Version             2>&1

Write-Host "Verificando que puede publicar (sesion start sin terminar)..." -ForegroundColor White

try {
    $inicio = Invoke-RestMethod -Method Post -Uri "$Base/$PageId/video_reels" `
        -Body @{ upload_phase = 'start'; access_token = $pagina.access_token } -TimeoutSec 60
} catch {
    Write-Host "FALLO la prueba: $(Detalle-Error $_)" -ForegroundColor Red
    exit 2
}

Write-Host ""
Write-Host "Listo. Token de pagina guardado y operativo." -ForegroundColor Green
Write-Host "  Pagina: $($pagina.name) ($PageId)" -ForegroundColor Green
Write-Host "  Prueba: sesion $($inicio.video_id) abierta y descartada" -ForegroundColor Green
Write-Host ""
Write-Host "La sesion de prueba se descarta sola; no se ha publicado nada." -ForegroundColor DarkGray
