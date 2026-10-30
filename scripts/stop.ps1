$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Split-Path $PSScriptRoot -Parent)).Path
$pidFile = Join-Path $projectPath 'logs\pids.json'
$processIds = @()
if (Test-Path -LiteralPath $pidFile) {
    $saved = Get-Content -LiteralPath $pidFile | ConvertFrom-Json
    $processIds += $saved.api, $saved.worker
}
$tunnelFile = Join-Path $projectPath 'logs\tunnel.pid'
if (Test-Path -LiteralPath $tunnelFile) { $processIds += [int](Get-Content -LiteralPath $tunnelFile) }
foreach ($processId in $processIds) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    if ($process -and $process.ExecutablePath -eq (Join-Path $projectPath '.venv\Scripts\python.exe')) {
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$processId"
        foreach ($child in $children) {
            if ($child.Name -in @('python.exe','cloudflared.exe')) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
        }
        Stop-Process -Id $processId -ErrorAction SilentlyContinue
    }
}
Write-Output 'Processos deste projeto encerrados. Os arquivos permanecem no E:.'
