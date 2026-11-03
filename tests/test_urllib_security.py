"""Fail closed on unexpected urllib schemes/hosts in legacy STT tools."""
import importlib.util
from pathlib import Path
from urllib.request import Request
import pytest

ROOT=Path(__file__).resolve().parents[1]/"scripts"


def load(filename):
    name=filename.replace(".py","")
    spec=importlib.util.spec_from_file_location(name,ROOT/filename)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename,accepted,rejected",[
    ("migrate_s7_data.py",
      ["http://127.0.0.1:8765/api/admin",
       "http://127.0.0.1:8765/api/admin/settings",
       "https://stt.faysk.dev/api/health"],
      ["file:///etc/passwd","http://127.0.0.1:8786/api/admin",
       "https://stt.faysk.dev.evil.example/api/health",
       "https://stt.faysk.dev/api/health?token=secret"]),
    ("setup_s7_tunnel.py",
      ["https://api.cloudflare.com/client/v4/zones?name=faysk.dev",
       "http://127.0.0.1:8786/api/health"],
      ["file:///etc/passwd", "https://api.cloudflare.com.evil.example/client/v4/zones",
       "http://127.0.0.1:8765/api/health",
       "https://api.cloudflare.com:444/client/v4/zones"])
])
def test_urllib_origin_allowlist_and_no_redirect(monkeypatch,filename,accepted,rejected):
    module=load(filename)
    seen=[]
    class FakeOpener:
        def open(self, request,timeout):
            seen.append((request.full_url,timeout))
            return object()
    monkeypatch.setattr(module.urllib.request,"build_opener",lambda *h:FakeOpener())
    for url in accepted:
        assert module.open_checked(Request(url),timeout=3)
    assert [uri for uri,timeout in seen]==accepted
    for url in rejected:
        with pytest.raises((ValueError,SystemExit)):
            module.open_checked(Request(url),timeout=3)
    assert len(seen)==len(accepted)
    assert module.NoRedirect().redirect_request(None) is None
