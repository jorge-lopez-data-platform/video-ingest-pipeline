# Prepara y despliega la Databricks App del bot de Slack.
#
# Por que hace falta un directorio aparte: una Databricks App instala el
# `requirements.txt` que esta en la raiz del codigo fuente, y en la raiz de
# este repo ese archivo es el del pipeline de video (faster-whisper, etc.).
# Desplegar la raiz instalaria el entorno del pipeline entero y NO instalaria
# slack_sdk, asi que el bot no arrancaria. `build_bot/` contiene solo lo del
# bot, con `requirements-bot.txt` copiado como `requirements.txt`.
#
# OJO con el directorio del paquete: tiene que quedar FUERA del repo.
# `databricks sync` respeta el .gitignore del repositorio que contiene el
# origen, asi que un `build_bot/` dentro del repo se sube VACIO (comprobado:
# `Initial Sync Complete` con cero ficheros) y `apps deploy` publicaria una app
# sin codigo. Por eso se compone en el temporal del usuario.
#
# Uso, desde la raiz del repo y con el perfil ya autenticado:
#   powershell -ExecutionPolicy Bypass -File jobs\desplegar_bot.ps1
param(
    [string]$App = "tiktok-slack-bot",
    [string]$Profile = "DEFAULT",
    [string]$WorkspacePath = ""
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$build = Join-Path $env:TEMP ("bot_build_" + [guid]::NewGuid().ToString("N").Substring(0, 8))

if (-not $WorkspacePath) { $WorkspacePath = "/Workspace/Apps/$App" }

# 1. Componer el paquete en un directorio nuevo (nunca reutilizado, para no
#    arrastrar restos de una corrida anterior).
New-Item -ItemType Directory -Path (Join-Path $build "slackbot") -Force | Out-Null

Copy-Item (Join-Path $raiz "app.yaml") $build
Copy-Item (Join-Path $raiz "requirements-bot.txt") (Join-Path $build "requirements.txt")
Get-ChildItem (Join-Path $raiz "slackbot") -Filter *.py |
    Copy-Item -Destination (Join-Path $build "slackbot")

Write-Host "paquete compuesto en $build :"
Get-ChildItem -Recurse $build -File | ForEach-Object { "  " + $_.FullName.Substring($build.Length + 1) }

# 2. Subir y desplegar.
databricks sync $build $WorkspacePath --profile $Profile --full
if ($LASTEXITCODE -ne 0) { throw "fallo al sincronizar el codigo de la app" }

# El sync puede terminar en verde sin haber subido nada. Se comprueba que
# app.yaml llego de verdad antes de desplegar: una app sin el comando de
# arranque no levanta.
$enWorkspace = databricks workspace list $WorkspacePath --profile $Profile 2>&1
if (-not ($enWorkspace | Select-String "app.yaml")) {
    throw "el codigo no llego a $WorkspacePath (app.yaml ausente); no se despliega"
}

databricks apps deploy $App --source-code-path $WorkspacePath --profile $Profile
if ($LASTEXITCODE -ne 0) { throw "fallo al desplegar la app" }

Write-Host ""
Write-Host "Logs:  databricks apps logs $App --profile $Profile"
Write-Host "Busca 'servidor de salud escuchando' y 'socket mode conectado'."
