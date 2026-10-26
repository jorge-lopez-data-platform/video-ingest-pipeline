<#
Borra los mp4 originales que fueron reemplazados por los `_v2`.

Solo elimina un original si su `_v2` existe y pesa más de 1 MB, es decir, si el
render nuevo terminó. Así, si la regeneración falló a medias, el video viejo
sigue ahí en lugar de quedarse sin ninguna versión.

Uso:
    .\limpiar_videos_viejos.ps1            # muestra qué borraría
    .\limpiar_videos_viejos.ps1 -Borrar    # borra de verdad
#>
param([switch]$Borrar)

$Volumen = "dbfs:/Volumes/tiktok/default/videos"
# `--long` es obligatorio: sin él la CLI devuelve solo el nombre del archivo y
# no hay tamaño que comparar, así que este script diría que no hay nada que borrar.
# Con `--long` cada línea es: FILE <bytes> <fecha ISO> <nombre>.
$Listado = databricks fs ls $Volumen --long --profile DEFAULT 2>$null
if ($LASTEXITCODE -ne 0) { throw "No se pudo listar $Volumen" }

$Nombres = @{}
foreach ($linea in $Listado) {
    $partes = ($linea -split '\s+') | Where-Object { $_ }
    if ($partes.Count -ge 4) { $Nombres[$partes[-1]] = [long]$partes[1] }
}
if ($Nombres.Count -eq 0) { throw "El listado de $Volumen llegó vacío o con formato inesperado." }

$Viejos = $Nombres.Keys |
    Where-Object { $_ -match '^motivacional_\d+\.mp4$' } |
    Sort-Object

if (-not $Viejos) { Write-Host "No hay originales que borrar."; exit 0 }

$BorrarLista = @()
foreach ($viejo in $Viejos) {
    $id = [regex]::Match($viejo, '\d+').Value
    $nuevo = "motivacional_${id}_v2.mp4"
    if (-not $Nombres.ContainsKey($nuevo)) {
        Write-Host "CONSERVAR $viejo (falta $nuevo)" -ForegroundColor Yellow
        continue
    }
    if ($Nombres[$nuevo] -lt 1MB) {
        Write-Host "CONSERVAR $viejo ($nuevo pesa $([math]::Round($Nombres[$nuevo]/1MB,2)) MB)" -ForegroundColor Yellow
        continue
    }
    Write-Host "BORRAR   $viejo (reemplazado por $nuevo)" -ForegroundColor Cyan
    $BorrarLista += @($viejo, "motivacional_${id}_publicacion.txt")
}

if (-not $Borrar) {
    Write-Host "`nModo simulacion. Repite con -Borrar para eliminar $($BorrarLista.Count) archivos."
    exit 0
}

foreach ($archivo in $BorrarLista) {
    if (-not $Nombres.ContainsKey($archivo)) { continue }
    databricks fs rm "$Volumen/$archivo" --profile DEFAULT 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "borrado $archivo" }
    else { Write-Host "FALLO al borrar $archivo" -ForegroundColor Red }
}
