$ErrorActionPreference = 'Stop'
$projectPath = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectPath
$runtime = Join-Path $projectPath '.venv\Scripts\python.exe'
$logs = Join-Path $projectPath 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $projectPath '.env'))) { throw 'Crie o .env antes de iniciar.' }
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { throw 'Porta 8765 já está em uso; não iniciarei processos duplicados.' }
$api = Start-Process -FilePath $runtime -ArgumentList '-m','uvicorn','server.app:app','--host','127.0.0.1','--port','8765','--no-access-log' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'api.log') -RedirectStandardError (Join-Path $logs 'api-error.log') -PassThru
$agent = Start-Process -FilePath $runtime -ArgumentList '-m','server.worker' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'worker.log') -RedirectStandardError (Join-Path $logs 'worker-error.log') -PassThru
@{api=$api.Id;worker=$agent.Id} | ConvertTo-Json | Set-Content (Join-Path $logs 'pids.json')
$envText = Get-Content -LiteralPath (Join-Path $projectPath '.env') -Raw
if ($envText -match 'TTS_TUNNEL_TOKEN=') {
  $tunnel = Start-Process -FilePath $runtime -ArgumentList '-m','server.tunnel' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'tunnel.log') -RedirectStandardError (Join-Path $logs 'tunnel-error.log') -PassThru
  $tunnel.Id | Set-Content (Join-Path $logs 'tunnel.pid')
}
Write-Output 'API e executor iniciados. Abra https://tts.faysk.dev'
