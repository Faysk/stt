[CmdletBinding()]
param([switch]$PreserveTunnel)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
$runtime = Join-Path $projectPath '.venv\Scripts\python.exe'
$ids = @()
$pidFile = Join-Path $projectPath 'logs\pids.json'
if (Test-Path -LiteralPath $pidFile) {
    $saved = Get-Content -LiteralPath $pidFile | ConvertFrom-Json
    $ids += @($saved.api, $saved.worker)
}
$tunnelFile = Join-Path $projectPath 'logs\tunnel.pid'
if (-not $PreserveTunnel -and (Test-Path -LiteralPath $tunnelFile)) { $ids += [int](Get-Content -LiteralPath $tunnelFile) }

function Stop-OwnedTree([int]$RootPid) {
    $root = Get-CimInstance Win32_Process -Filter "ProcessId=$RootPid" -ErrorAction SilentlyContinue
    if (-not $root) { return }
    # A stale PID file must never terminate an unrelated executable.
    if ($root.ExecutablePath -ne $runtime) { return }
    if ($root.CommandLine -notmatch 'server\.(app|worker|tunnel)|uvicorn') { return }
    $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    $children = @{}
    foreach ($process in $all) {
        $parent = [int]$process.ParentProcessId
        if (-not $children.ContainsKey($parent)) { $children[$parent] = @() }
        $children[$parent] += $process
    }
    function Stop-Descendants([int]$Parent, [System.Collections.IDictionary]$Tree) {
        foreach ($child in @($Tree[$Parent])) {
            if (-not $child) { continue }
            Stop-Descendants -Parent ([int]$child.ProcessId) -Tree $Tree
            # Verify current parent PID against the captured tree before stopping.
            $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.ProcessId)" -ErrorAction SilentlyContinue
            if ($current -and $current.ParentProcessId -eq $Parent -and $current.CreationDate -eq $child.CreationDate) {
                Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Stop-Descendants -Parent $RootPid -Tree $children
    $current = Get-CimInstance Win32_Process -Filter "ProcessId=$RootPid" -ErrorAction SilentlyContinue
    if ($current -and $current.CreationDate -eq $root.CreationDate -and $current.ExecutablePath -eq $runtime) {
        Stop-Process -Id $RootPid -Force -ErrorAction SilentlyContinue
    }
}
foreach ($id in ($ids | Where-Object { $_ -match '^\d+$' } | Sort-Object -Unique)) { Stop-OwnedTree -RootPid ([int]$id) }
Write-Output 'Processos verificados deste projeto encerrados. Resultados pendentes na outbox foram preservados.'
if ($PreserveTunnel) { Write-Output 'STT_TUNNEL_PRESERVED: Tunnel excluded from stop; no connector processes touched.' }
