"""Regression: release must not restart Uvicorn while TCP 8765 is still listening.

These checks inspect the operator-controlled script without executing a deploy
or touching any production processes.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "publish-stt.ps1"


def test_release_waits_after_stop_before_start():
    source = RELEASE.read_text(encoding="utf-8")
    stop = source.index("& (Join-Path $root 'scripts\\stop.ps1')")
    wait = source.index("Wait-SttPortReleased -Port 8765 -TimeoutSeconds 45")
    start = source.index("& (Join-Path $root 'scripts\\start.ps1')")
    deploy = source.index("Invoke-Native 'npm' @('run','deploy')")
    assert stop < wait < start < deploy


def test_wait_is_bounded_and_fail_closed_for_active_listener():
    source = RELEASE.read_text(encoding="utf-8")
    block = source.split("function Wait-SttPortReleased", 1)[1].split("function Test-SttHttp", 1)[0]
    assert "Get-NetTCPConnection -LocalPort $Port -State Listen" in block
    assert "Where-Object { $null -ne $_ }" in block
    assert "if ($listeners.Count -eq 0)" in block
    assert "if ($timer.Elapsed.TotalSeconds -ge $TimeoutSeconds)" in block
    assert "throw \"STT_PORT_NOT_RELEASED:" in block
    assert "Start-Sleep -Milliseconds 400" in block


def test_release_reuses_verified_node_dependencies_without_mutating_them():
    source = RELEASE.read_text(encoding="utf-8")
    assert "Invoke-Native 'node' @('scripts/verify-node-deps.mjs')" in source
    assert "Invoke-Native 'npm' @('ci')" not in source
    assert source.index("verify-node-deps.mjs") < source.index("Running local Python and Node regressions")


def test_operator_release_preserves_persistent_cloudflare_tunnel():
    publish = RELEASE.read_text(encoding="utf-8")
    stop = (ROOT / "scripts" / "stop.ps1").read_text(encoding="utf-8")
    start = (ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8")
    assert "& (Join-Path $root 'scripts\\stop.ps1') -PreserveTunnel" in publish
    assert "& (Join-Path $root 'scripts\\start.ps1') -SkipTunnel" in publish
    assert "param([switch]$PreserveTunnel)" in stop
    assert "if (-not $PreserveTunnel -and (Test-Path -LiteralPath $tunnelFile))" in stop
    assert "param([switch]$SkipTunnel)" in start
    assert "if (-not $SkipTunnel -and $envText -match" in start
    # Never mutate tunnel while the production publish script is in-flight.
    assert "server.tunnel" not in publish
    assert "cloudflared" not in publish
    # Reached only after public HTTPS origin gate; avoids silently starting
    # application with a missing/misconfigured persistent connector.
    assert publish.index("Test-SttHttp 'before-restart'") < publish.index(
        "& (Join-Path $root 'scripts\\stop.ps1') -PreserveTunnel")
