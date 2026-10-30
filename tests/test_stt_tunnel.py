"""Tests for the STT-only named Cloudflare Tunnel runner (no real credentials)."""
import subprocess
from pathlib import Path

import pytest

from server import tunnel


def test_named_tunnel_uses_wranger_auth_without_token(monkeypatch):
    monkeypatch.setattr(tunnel, 'load_dotenv', lambda: None)
    monkeypatch.setenv('STT_TUNNEL_NAME', 'faysk-stt')
    monkeypatch.delenv('STT_TUNNEL_TOKEN', raising=False)
    original_file = Path.is_file
    monkeypatch.setattr(Path, 'is_file',
                        lambda p: p.name == 'wrangler.js' or original_file(p))
    invoked = []
    monkeypatch.setattr(subprocess, 'run',
                        lambda args, **kwargs: invoked.append((args, kwargs)))
    tunnel.main()
    assert len(invoked) == 1
    args, kwargs = invoked[0]
    assert args[0] == 'node'
    assert args[-3:] == ['tunnel', 'run', 'faysk-stt']
    assert kwargs['check'] is True
    assert not any('eyJ' in str(v) for v in args)


@pytest.mark.parametrize('name,token', [
    ('', ''),
    ('faysk-stt', 'synthetic-secret'),
    ('other-tunnel', ''),
])
def test_tunnel_rejects_ambiguous_or_wrong_configuration(monkeypatch, name, token):
    monkeypatch.setattr(tunnel, 'load_dotenv', lambda: None)
    monkeypatch.setenv('STT_TUNNEL_NAME', name)
    monkeypatch.setenv('STT_TUNNEL_TOKEN', token)
    monkeypatch.setattr(subprocess, 'run', lambda *_a, **_kw: pytest.fail('Tunnel spawned'))
    with pytest.raises(RuntimeError):
        tunnel.main()


def test_token_mode_preserves_existing_stt_connector_mode(monkeypatch):
    monkeypatch.setattr(tunnel, 'load_dotenv', lambda: None)
    monkeypatch.setenv('STT_TUNNEL_NAME', '')
    monkeypatch.setenv('STT_TUNNEL_TOKEN', 'private-fixture')
    monkeypatch.setenv('STT_CLOUDFLARED', 'cloudflared')
    commands = []
    monkeypatch.setattr(subprocess, 'run', lambda argv, **kw: commands.append((argv, kw)))
    tunnel.main()
    assert commands[0][0] == ['cloudflared', 'tunnel', '--no-autoupdate', 'run']
    assert commands[0][1]['env']['TUNNEL_TOKEN'] == 'private-fixture'
