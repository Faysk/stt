#Requires -Version 7.0
<# Operator-run STT application release.
   Does not deploy by default; -Apply and -QueueDrained required.
   Changes only local Faysk/stt checkout/processes and Cloudflare Worker faysk-stt.
   Never deletes SQLite, outbox, models, Cloudflare Tunnel or DNS.
   Requires existing local .env / Wrangler OAuth. #>
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$QueueDrained
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$originalCutover = [Environment]::GetEnvironmentVariable('STT_APPROVED_CUTOVER','Process')
$originalWorker = [Environment]::GetEnvironmentVariable('STT_APPROVED_WORKER','Process')

function Invoke-Native([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "COMMAND_FAILED: $Program (exit code $LASTEXITCODE)"
    }
}

function HttpCode([string]$Url) {
    try {
        return [int](Invoke-WebRequest -Uri $Url -TimeoutSec 25 -MaximumRedirection 0 -SkipHttpErrorCheck).StatusCode
    } catch {
        return 0
    }
}

function Wait-SttPortReleased(
    [int]$Port = 8765,
    [int]$TimeoutSeconds = 45,
    [int]$StableMilliseconds = 3000
) {
    # A single empty TCP snapshot is not evidence that Uvicorn has stopped.
    # A supervisor may immediately relaunch it while the release is starting.
    $total = [System.Diagnostics.Stopwatch]::StartNew()
    $free = $null
    $reappeared = $false
    while ($total.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        $listeners = @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Where-Object { $null -ne $_ }
        )
        if ($listeners.Count -eq 0) {
            if ($null -eq $free) {
                $free = [System.Diagnostics.Stopwatch]::StartNew()
            }
            if ($free.Elapsed.TotalMilliseconds -ge $StableMilliseconds) {
                Write-Host ("Port {0} had no listeners for {1} ms; attempting startup." -f $Port,$StableMilliseconds)
                return
            }
        } else {
            if ($null -ne $free -and -not $reappeared) {
                Write-Warning "Port $Port became occupied again after briefly being free; possible external restart."
                $reappeared = $true
            }
            $free = $null
        }
        Start-Sleep -Milliseconds 400
    }
    # Do not claim a free port, kill an unknown owner or proceed with deploy.
    $listeners = @(
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $null -ne $_ }
    )
    $owners = if ($listeners.Count) {
        (@($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', ')
    } else {
        '(port not stable for required interval)'
    }
    throw "STT_PORT_NOT_RELEASED: port $Port did not remain free for $StableMilliseconds ms in $TimeoutSeconds seconds. Listener PID(s): $owners. Inspect owner before retrying; Tunnel preserved."
}

function Test-SttHttp([string]$Phase, [int]$Attempts = 1) {
    $targets = @(
        @{Name='Frontend'; Url='https://stt.faysk.dev/'; Expected=200}
        @{Name='API'; Url='https://stt.faysk.dev/api/health'; Expected=200}
        @{Name='Origin without secret'; Url='https://stt-origin.faysk.dev/api/health'; Expected=403}
    )
    for ($attempt=1; $attempt -le $Attempts; $attempt++) {
        $allPassed = $true
        foreach ($target in $targets) {
            $actual = HttpCode $target.Url
            $ok = ($actual -eq $target.Expected)
            if (-not $ok) { $allPassed = $false }
            Write-Host ("[{0}] {1}: HTTP {2} (expected {3}) {4}" -f $Phase, $target.Name, $actual, $target.Expected, $(if($ok){'OK'}else{'FAILED'}))
        }
        if ($allPassed) { return }
        if ($attempt -lt $Attempts) { Start-Sleep -Seconds 4 }
    }
    throw "STT_HTTP_GATE_FAILED: $Phase"
}

try {
    Set-Location -LiteralPath $root
    foreach ($program in @('git', 'npm', 'node')) {
        if (-not (Get-Command $program -ErrorAction SilentlyContinue)) {
            throw "MISSING_PROGRAM: $program"
        }
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'MISSING_PYTHON: existing .venv\Scripts\python.exe required'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $root '.env') -PathType Leaf)) {
        throw 'MISSING_ENV: existing private .env required; will never generate or display it'
    }

    $remote = (& git remote get-url origin | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $remote -notmatch '(?i)github\.com[:/]Faysk/(stt|tts)(?:\.git)?/?$') {
        throw 'UNEXPECTED_GIT_REMOTE: only Faysk/stt (or historical redirected Faysk/tts) allowed'
    }
    $branch = (& git branch --show-current | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -cne 'main') {
        throw 'UNEXPECTED_BRANCH: checkout main before publishing'
    }
    $dirty = @(& git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0 -or $dirty.Count -gt 0) {
        throw 'DIRTY_TRACKED_FILES: review/stash local tracked changes first'
    }
    $current = (& git rev-parse HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'GIT_SHA_FAILED' }

    Write-Host '==== STT operator release ===='
    Write-Host "Checkout: $root"
    Write-Host "Current commit: $current"
    Write-Host 'Target: local API + worker restart and Cloudflare Worker faysk-stt only.'
    Write-Host 'Preserved: SQLite, blob, models, worker-outbox, Tunnel faysk-stt, DNS.'
    if (-not $Apply) {
        Write-Host 'READ-ONLY PREVIEW: no git pull, installs, restart or deploy.'
        Write-Host 'Review the live queue, wait until there are NO running or queued jobs.'
        Write-Host 'Then execute: pwsh -NoProfile -ExecutionPolicy Bypass -File .\scripts\publish-stt.ps1 -Apply -QueueDrained'
        return
    }
    if (-not $QueueDrained) {
        throw 'QUEUE_NOT_CONFIRMED: operator must verify no queued/running jobs, then pass -QueueDrained'
    }

    Write-Host 'Updating main (fast-forward only)...'
    Invoke-Native 'git' @('pull','--ff-only','origin','main')
    $sha = (& git rev-parse HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'GIT_SHA_FAILED' }
    Write-Host "Release checkout: $sha"
    Write-Host 'Checking existing Node dependencies against package-lock.json (read only)...'
    # npm ci unlinks all installed packages and fails on Windows when Wrangler
    # keeps esbuild.exe in use. Provision dependencies separately if mismatched.
    Invoke-Native 'node' @('scripts/verify-node-deps.mjs')
    Write-Host 'Reusing verified Node dependencies; npm ci skipped.'

    Write-Host 'Running local Python and Node regressions...'
    Invoke-Native $python @('-m','pytest','-q')
    Invoke-Native 'npm' @('run','check')
    Invoke-Native 'node' @('tests/i18n.test.cjs')
    Invoke-Native 'node' @('tests/edge.test.cjs')
    Invoke-Native 'node' @('tests/deploy_guard.test.cjs')
    Write-Host 'Cloudflare Worker dry-run (no deployment)...'
    Invoke-Native 'npm' @('run','deploy:stt:dry-run')

    Test-SttHttp 'before-restart'
    Write-Host 'Stopping application API/worker only; preserving the existing Cloudflare Tunnel...'
    & (Join-Path $root 'scripts\stop.ps1') -PreserveTunnel
    Write-Host 'Waiting for the old API listener to release port 8765...'
    Wait-SttPortReleased -Port 8765 -TimeoutSeconds 45
    Write-Host 'Starting API and worker from this checkout; the existing Tunnel remains untouched...'
    & (Join-Path $root 'scripts\start.ps1') -SkipTunnel
    Test-SttHttp 'after-restart' 6

    Write-Host 'Publishing only the approved Cloudflare Worker faysk-stt...'
    $env:STT_APPROVED_CUTOVER = 'stt.faysk.dev'
    $env:STT_APPROVED_WORKER = 'faysk-stt'
    Invoke-Native 'npm' @('run','deploy')
    Test-SttHttp 'after-worker-deploy' 6

    $adminAsset = HttpCode 'https://stt.faysk.dev/admin-enhancements.js'
    if ($adminAsset -ne 200) {
        throw "ADMIN_ASSET_MISSING: admin-enhancements.js HTTP $adminAsset"
    }
    Write-Host "Admin enhancement asset: HTTP $adminAsset OK"
    Write-Host "STT_RELEASE_PUBLISHED: $sha"
    Write-Host 'Next: operator must test a new transcription, download, model preload and GPU/CPU; this script does NOT certify them.'
}
finally {
    [Environment]::SetEnvironmentVariable('STT_APPROVED_CUTOVER', $originalCutover, 'Process')
    [Environment]::SetEnvironmentVariable('STT_APPROVED_WORKER', $originalWorker, 'Process')
}
