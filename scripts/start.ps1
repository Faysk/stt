[CmdletBinding()]
param([switch]$SkipTunnel)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectPath
$runtime = Join-Path $projectPath '.venv\Scripts\python.exe'
$logs = Join-Path $projectPath 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $projectPath '.env'))) { throw 'Crie o .env antes de iniciar.' }
$listeners = @(
    Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $null -ne $_ }
)
if ($listeners.Count -gt 0) {
    # Capture the owner only. CommandLine can contain secrets; never print it.
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
        $ownerPid = [int]$_
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
        "{0} ({1})" -f $ownerPid, $(if ($owner) { $owner.Name } else { 'unknown' })
    })
    throw ("STT_API_PORT_OCCUPIED: porta 8765 já tem listener PID(s): {0}. Não iniciarei API duplicada. Diagnostique o dono antes de encerrar qualquer processo." -f ($owners -join ', '))
}
$api = Start-Process -FilePath $runtime -ArgumentList '-m','uvicorn','server.app:app','--host','127.0.0.1','--port','8765','--no-access-log' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'api.log') -RedirectStandardError (Join-Path $logs 'api-error.log') -PassThru
$agent = Start-Process -FilePath $runtime -ArgumentList '-m','server.worker' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'worker.log') -RedirectStandardError (Join-Path $logs 'worker-error.log') -PassThru
@{api=$api.Id;worker=$agent.Id} | ConvertTo-Json | Set-Content (Join-Path $logs 'pids.json')
$envText = Get-Content -LiteralPath (Join-Path $projectPath '.env') -Raw
if (-not $SkipTunnel -and $envText -match '(?m)^STT_TUNNEL_(?:NAME|TOKEN)=') {
  $tunnel = Start-Process -FilePath $runtime -ArgumentList '-m','server.tunnel' -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logs 'tunnel.log') -RedirectStandardError (Join-Path $logs 'tunnel-error.log') -PassThru
  $tunnel.Id | Set-Content (Join-Path $logs 'tunnel.pid')
}
if ($SkipTunnel) {
  Write-Output 'STT_TUNNEL_PRESERVED: API e worker iniciados sem alterar o Tunnel existente.'
} else {
  Write-Output 'API e executor iniciados. Tunnel iniciado apenas se configurado.'
}
