"""No-write Cloudflare plan, Worker separation and STT SEO metadata tests."""
import json

import pytest

from scripts import cloudflare


def test_legacy_provisioner_is_offline_plan_by_default(monkeypatch, capsys):
    monkeypatch.delenv('CLOUDFLARE_ACCOUNT_ID', raising=False)
    monkeypatch.delenv('CLOUDFLARE_API_TOKEN', raising=False)
    monkeypatch.delenv('CF_CREDENTIAL_FILE', raising=False)
    monkeypatch.setattr(cloudflare, 'client', None)
    cloudflare.main([])
    result = json.loads(capsys.readouterr().out)
    assert result['mode'] == 'plan-only'
    assert result['writes_performed'] is False
    assert result['target'] == 'faysk-tts'
    with pytest.raises(SystemExit):
        cloudflare.main(['--apply-legacy'])


def test_separate_worker_config_never_targets_old_domain():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    old = json.loads((root / 'wrangler.jsonc').read_text())
    new = json.loads((root / 'wrangler.stt.jsonc').read_text())
    assert old['name'] == 'faysk-tts'
    assert new['name'] == 'faysk-stt'
    assert old['routes'][0]['pattern'] == 'tts.faysk.dev'
    assert new['routes'][0] == {'pattern':'stt.faysk.dev','custom_domain':True}
    # No new tunnel needs provisioning until the physical origin is tested.
    assert new['vars']['ORIGIN'] == old['vars']['ORIGIN']
    assert new['assets']['run_worker_first'] == ['/api/*']


def test_stt_public_metadata_is_canonical_and_preserves_old_repo_link():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'web'
    index = (root / 'index.html').read_text(encoding='utf-8')
    admin = (root / 'admin.html').read_text(encoding='utf-8')
    manifest = json.loads((root / 'site.webmanifest').read_text(encoding='utf-8'))
    sitemap = (root / 'sitemap.xml').read_text(encoding='utf-8')
    robots = (root / 'robots.txt').read_text(encoding='utf-8')
    assert 'https://stt.faysk.dev/' in index
    assert 'rel="canonical" href="https://tts.faysk.dev/"' not in index
    assert 'id="legacy-access"' in index
    assert 'href="https://tts.faysk.dev/"' in index
    assert 'stt / faysk' in index
    assert 'Operação / stt faysk' in admin
    assert manifest['short_name'] == 'stt'
    assert 'https://stt.faysk.dev/' in sitemap
    assert 'stt.faysk.dev/sitemap.xml' in robots
    # Deliberately keep the real repository link until an authorized rename.
    assert 'https://github.com/Faysk/tts' in index
