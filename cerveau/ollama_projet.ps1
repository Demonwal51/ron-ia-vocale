# Start the project's own hidden Ollama server on port 11435, with the project's own model folder (never the
# Ollama a player may already run on 11434, nor its models). Prints the PID. ASCII only.
# Which ollama.exe: "ollama_exe" in config.json, else the one on the PATH, else Ollama's default install folder.
$racine = Split-Path -Parent $PSScriptRoot
$exe = $null
$cfg = Join-Path $racine 'config.json'
if (Test-Path $cfg) {
    try { $exe = (Get-Content $cfg -Raw | ConvertFrom-Json).ollama_exe } catch {}
}
if (-not $exe -or -not (Test-Path $exe)) { $exe = (Get-Command ollama.exe -ErrorAction SilentlyContinue).Source }
if (-not $exe -or -not (Test-Path $exe)) { $exe = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
if (-not (Test-Path $exe)) { "ECHEC : ollama.exe introuvable (installe Ollama, ou mets ollama_exe dans config.json)"; exit 1 }
$ok = $false
try { $null = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -TimeoutSec 2; $ok = $true } catch {}
if ($ok) { 'deja lance sur 11435'; exit 0 }
$env:OLLAMA_HOST = '127.0.0.1:11435'
$env:OLLAMA_MODELS = Join-Path $racine 'modeles'
$env:OLLAMA_KEEP_ALIVE = '30m'
$env:OLLAMA_MAX_LOADED_MODELS = '1'
# llama-server keeps past prompts in the PC's memory, up to 8 GB by default (02/11: 59 prompts, 8.1 GB, and the
# PC could not load Whisper any more). Every question here is new: no prompt cache. (llama-server reads LLAMA_ARG_*)
$env:LLAMA_ARG_CACHE_RAM = '0'
$log = Join-Path $PSScriptRoot 'ollama_projet.log'
$p = Start-Process -FilePath $exe -ArgumentList 'serve' -WindowStyle Hidden -PassThru -RedirectStandardError $log
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try { $v = Invoke-RestMethod -Uri 'http://127.0.0.1:11435/api/version' -TimeoutSec 2; "PID $($p.Id) pret, version $($v.version)"; exit 0 } catch {}
}
"ECHEC : pas de reponse sur 11435 (PID $($p.Id))"; exit 1
