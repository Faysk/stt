# Provision a unique scoped S7 key and copy the native worker without enabling jobs.
# Preview by default. Apply requires explicit operator action.
[CmdletBinding()]
param(
  [switch]$Apply,
  [Parameter(Mandatory=$true)][string]$S7Host,
  [Parameter(Mandatory=$true)][string]$S7User,
  [ValidateRange(1,65535)][int]$S7Port = 8022
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repo '.env'
$workerPath = Join-Path $repo 's7\worker.py'
if (-not (Test-Path -LiteralPath $envPath) -or -not (Test-Path -LiteralPath $workerPath)) {
  throw 'O checkout precisa conter .env privado e s7/worker.py'
}
if ($S7Host -notmatch '^[a-zA-Z0-9.:-]{1,253}$' -or $S7User -notmatch '^[a-zA-Z0-9_-]{1,80}$') {
  throw 'Destino SSH invalido.'
}
if (-not $Apply) {
  Write-Host 'PREVIEW: criaria/reutilizaria chave dedicada no .env privado, instalaria chave no Termux e copiaria s7/worker.py.'
  Write-Host 'O script NAO inicia o worker, NAO habilita jobs e NAO reinicia o STT.'
  return
}
$existing = Get-Content -LiteralPath $envPath -Raw -Encoding UTF8
$m = [regex]::Match($existing,'(?m)^STT_S7_WORKER_KEY=([0-9a-fA-F]{64})\s*$')
if ($m.Success) {
  $secret = $m.Groups[1].Value
} elseif ($existing -match '(?m)^STT_S7_WORKER_KEY=') {
  throw 'STT_S7_WORKER_KEY ja existe com formato inesperado. Nao sobrescrever.'
} else {
  $secret = [Convert]::ToHexString(
    [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
  [IO.File]::AppendAllText($envPath, "`nSTT_S7_WORKER_KEY=$secret`nSTT_S7_ENABLED=0`n")
}
try {
  $target = "$S7User@$S7Host"
  # SSH reads key from stdin, not process arguments or history. Password prompt uses tty.
  $secret | & ssh -p $S7Port $target 'umask 077; cat > "$HOME/.stt-s7-worker-key"; chmod 600 "$HOME/.stt-s7-worker-key"'
  if ($LASTEXITCODE -ne 0) { throw 'Falha ao provisionar chave no S7.' }
  & scp -P $S7Port $workerPath "${target}:stt-lab/stt-worker.py"
  if ($LASTEXITCODE -ne 0) { throw 'Falha ao copiar s7/worker.py.' }
  Write-Host 'OK: chave privada provisionada, worker copiado. Nenhum job foi ativado.'
  Write-Host 'Instale ffmpeg no Termux e reinicie a API apos revisar STT_S7_ENABLED.'
} finally {
  $secret = $null
}
