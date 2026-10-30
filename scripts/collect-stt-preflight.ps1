# Read-only, privacy-minimal diagnostic for the STT operator gate (#28).
# Does not read .env, database, media, secrets, user names or local addresses.
param([switch]$SkipDns)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path

function Version-Of([string]$Command) {
    if (-not (Get-Command -Name $Command -ErrorAction SilentlyContinue)) { return $null }
    try {
        $lines = & $Command --version 2>&1 | Select-Object -First 2
        return (($lines -join ' ').Trim() -replace '\s+', ' ').Substring(
            0, [Math]::Min(150, (($lines -join ' ').Trim()).Length))
    } catch { return $null }
}

$gitSha = $null
$dirtyFilesCount = $null
if (Get-Command git -ErrorAction SilentlyContinue) {
    try {
        $gitSha = (& git -C $root rev-parse HEAD 2>$null | Select-Object -First 1).Trim()
        $dirtyFilesCount = @(& git -C $root status --porcelain 2>$null).Count
    } catch { $gitSha = $null }
}
$gpus = @()
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    try {
        $rows = @(& nvidia-smi '--query-gpu=name,driver_version,memory.total' '--format=csv,noheader,nounits' 2>$null)
        foreach ($line in $rows) {
            $fields = @($line -split ',' | ForEach-Object { $_.Trim() })
            if ($fields.Count -eq 3) {
                $gpus += [ordered]@{ model=$fields[0]; driver=$fields[1]; vram_mb=$fields[2] }
            }
        }
    } catch { }
}
$portState = 'unknown'
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    try {
        $listening = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
        $portState = if ($listening.Count -gt 0) { 'listening' } else { 'not-listening' }
    } catch { }
}
$domains = [ordered]@{}
if (-not $SkipDns -and (Get-Command Resolve-DnsName -ErrorAction SilentlyContinue)) {
    foreach ($domain in @('tts.faysk.dev','stt.faysk.dev','tts-origin.faysk.dev','stt-origin.faysk.dev')) {
        try {
            $answers = @(Resolve-DnsName -Name $domain -ErrorAction Stop |
                Where-Object { $_.Type -in @('A','AAAA','CNAME') } |
                ForEach-Object {
                    if ($_.IPAddress) { [string]$_.IPAddress }
                    elseif ($_.NameHost) { [string]$_.NameHost }
                } | Select-Object -Unique)
            $domains[$domain] = @{status='ok'; answers=$answers}
        } catch { $domains[$domain] = @{status='unresolved'; answers=@()} }
    }
}
$report = [ordered]@{
    schema_version = 1
    collected_at_utc = [DateTime]::UtcNow.ToString('o')
    mode = 'read-only'
    data_access = 'none'
    secrets_access = 'none'
    git_commit = $gitSha
    uncommitted_entries_count = $dirtyFilesCount
    powershell_version = $PSVersionTable.PSVersion.ToString()
    python_version = Version-Of 'python'
    node_version = Version-Of 'node'
    gpu = $gpus
    api_port_8765 = $portState
    public_dns = $domains
}
$dir = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$destination = Join-Path $dir 'stt-operator-preflight.json'
$report | ConvertTo-Json -Depth 7 | Set-Content -LiteralPath $destination -Encoding utf8
Write-Output 'Relatorio somente leitura em logs/stt-operator-preflight.json. Revise antes de compartilhar; nunca envie .env, DB, tokens ou arquivos de midia.'
