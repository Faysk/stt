"""Public social-link admission, restricted extractors and bounded failures."""
import importlib
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from server.video_links import video_link, VideoLinkError, resolve_video_link
from server import video_download as download


VALID=[
    ('https://youtu.be/BaW_jenozKc?si=tracking','YouTube','https://www.youtube.com/watch?v=BaW_jenozKc'),
    ('https://www.youtube.com/shorts/BaW_jenozKc','YouTube','https://www.youtube.com/watch?v=BaW_jenozKc'),
    ('https://www.instagram.com/reel/ABCdef123_/?igsh=tracking','Instagram','https://www.instagram.com/reel/ABCdef123_/'),
    ('https://instagram.com/p/ABCdef123/','Instagram','https://www.instagram.com/p/ABCdef123/'),
    ('https://www.tiktok.com/@sample/video/7100000000000000000?share=1','TikTok','https://www.tiktok.com/@sample/video/7100000000000000000'),
    ('https://vm.tiktok.com/ZMabcdefg/','TikTok','https://vm.tiktok.com/ZMabcdefg/'),
    ('https://www.facebook.com/reel/1234567890123/?share=1','Facebook','https://www.facebook.com/reel/1234567890123/'),
    ('https://m.facebook.com/watch/?v=1234567890123&tracking=1','Facebook','https://www.facebook.com/watch/?v=1234567890123'),
    ('https://www.facebook.com/example/videos/1234567890123/','Facebook','https://www.facebook.com/example/videos/1234567890123/'),
    ('https://fb.watch/ABC_def/','Facebook','https://fb.watch/ABC_def/'),
    ('https://www.facebook.com/share/r/ABCdef123/','Facebook','https://www.facebook.com/share/r/ABCdef123/'),
    ('https://x.com/example/status/1234567890123?s=20','X','https://x.com/i/web/status/1234567890123'),
    ('https://twitter.com/example/status/1234567890123/video/2','X','https://x.com/i/web/status/1234567890123/video/2'),
    ('x.com/example/status/1234567890123','X','https://x.com/i/web/status/1234567890123'),
]


@pytest.mark.parametrize('value,platform,canonical',VALID)
def test_known_video_links_normalize_and_remove_tracking(value,platform,canonical):
    result=video_link(value)
    assert result.platform==platform and result.url==canonical


@pytest.mark.parametrize('value',[
    'https://127.0.0.1/video','http://www.youtube.com/watch?v=BaW_jenozKc',
    'https://instagram.com.evil.example/reel/ABCdef123/',
    'https://instagram.com@127.0.0.1/reel/ABCdef123/',
    'https://instagram.com:8443/reel/ABCdef123/','https://instagram.com:bad/reel/ABCdef123/',
    'https://instagram.com/sample/','https://www.tiktok.com/@sample',
    'https://www.facebook.com/example/','https://x.com/example',
    'https://youtube.com/playlist?list=abcdef','https://www.youtube.com/@sample/videos',
    'https://facebook.com/flx/warn/?u=https://127.0.0.1/',
    'https://www.facebook.com/plugins/video.php?href=https://127.0.0.1/',
    'https://www.instagram.com/reel/../secret','https://www.instagram.com/reel/%2fsecret',
    'https://www.instagram.com\\@127.0.0.1/reel/ABCdef123/',
    'https://www.instagram.com/\nreel/ABCdef123/','file:///etc/passwd',
])
def test_untrusted_hosts_profiles_playlists_and_unsafe_syntax_are_rejected(value):
    with pytest.raises(VideoLinkError):video_link(value)


def test_share_redirects_are_validated_before_following(monkeypatch):
    import server.video_links as links
    seen=[]
    class Response:
        status=302
        headers={'Location':'http://127.0.0.1/private'}
        def close(self):pass
    class Opener:
        def open(self,request,timeout):seen.append(request.full_url);return Response()
    monkeypatch.setattr(links,'build_opener',lambda *args:Opener())
    with pytest.raises(VideoLinkError):resolve_video_link('https://fb.watch/ABCdef123/')
    assert seen==['https://fb.watch/ABCdef123/']
    Response.headers={'Location':'https://www.facebook.com/reel/1234567890123/'}
    assert resolve_video_link('https://fb.watch/ABCdef123/').url=='https://www.facebook.com/reel/1234567890123/'


def test_extractor_allowlist_contains_only_requested_single_video_providers():
    import yt_dlp
    with yt_dlp.YoutubeDL({'allowed_extractors':download.EXTRACTORS,'quiet':True}) as ydl:
        assert set(ydl._ies)=={'Youtube','Instagram','TikTok','TikTokVM','Facebook','FacebookReel','Twitter'}
        for value,_,_ in VALID:
            link=video_link(value)
            if not link.short:assert any(ie.suitable(link.url) for ie in ydl._ies.values())


def test_shared_download_caps_media_and_reports_safe_failures(tmp_path,monkeypatch):
    import yt_dlp
    captured={}
    class Downloader:
        def __init__(self,options):captured.update(options)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def extract_info(self,url,download):
            Path(captured['outtmpl']).write_bytes(b'fixture media')
            captured['progress_hooks'][0]({'downloaded_bytes':5,'total_bytes':10})
            return {'title':'Public reference'}
    monkeypatch.setattr(yt_dlp,'YoutubeDL',Downloader)
    progress=[]
    result=download.download_video(VALID[0][0],tmp_path/'input.media',progress.append)
    assert result['title']=='Public reference' and progress==[50]
    assert captured['playlist_items']=='1' and captured['skip_unavailable_fragments'] is False
    assert not {'cookiefile','cookiesfrombrowser','username','password'} & captured.keys()
    with pytest.raises(download.VideoDownloadError,match='LINK_LIMIT'):
        captured['progress_hooks'][0]({'downloaded_bytes':download.MAX_BYTES+1})
    with pytest.raises(download.VideoDownloadError,match='LINK_UNAVAILABLE'):
        captured['match_filter']({'is_live':True})
    with pytest.raises(download.VideoDownloadError,match='LINK_LIMIT'):
        captured['match_filter']({'duration':7201})
    with pytest.raises(download.VideoDownloadError,match='LINK_NO_AUDIO'):
        captured['match_filter']({'formats':[{'acodec':'none'}]})


@pytest.fixture
def api(tmp_path,monkeypatch):
    for name,value in {'STT_DATA':str(tmp_path),'STT_ADMIN_KEY':'test-admin',
        'STT_WORKER_KEY':'test-worker','STT_REQUIRE_EDGE':'0','STT_LIGHT_HOST':'0'}.items():monkeypatch.setenv(name,value)
    with patch('dotenv.load_dotenv',lambda *a,**kw:None):
        import server.app as m
        m=importlib.reload(m)
        with TestClient(m.app) as c:yield c,m


@pytest.mark.parametrize('value,platform,canonical',[VALID[i] for i in (0,2,4,6,11)])
def test_one_link_endpoint_admits_all_platforms_and_preserves_ownership(api,value,platform,canonical):
    c,m=api;session={'x-session-key':'a'*64}
    assert c.post('/api/jobs/link',json={'url':value}).status_code==401
    r=c.post('/api/jobs/link',headers=session,json={'url':value})
    assert r.status_code==201
    jid=r.json()['id']
    assert c.get('/api/jobs/'+jid,headers=session).json()['title']==platform
    assert c.get('/api/jobs/'+jid,headers={'x-session-key':'b'*64}).status_code==404
    claim=c.post('/api/worker/claim',headers={'x-worker-key':'test-worker'},json={'id':'notebook'}).json()['job']
    assert claim['id']==jid and claim['source']==canonical


def test_new_link_endpoint_obeys_network_admission_rate_limits(api):
    c,m=api
    for _ in range(10):
        assert c.post('/api/jobs/link',headers={'x-session-key':'a'*64},json={'url':VALID[0][0]}).status_code==201
    assert c.post('/api/jobs/link',headers={'x-session-key':'a'*64},json={'url':VALID[2][0]}).status_code==429


def test_social_links_respect_limited_host_mode(api,monkeypatch):
    c,m=api
    monkeypatch.setenv('STT_LIGHT_HOST','1')
    health=c.get('/api/health').json()
    assert health['links'] is False and health['youtube'] is False
    response=c.post('/api/jobs/link',headers={'x-session-key':'a'*64},json={'url':VALID[2][0]})
    assert response.status_code==503
    with m.db() as connection:assert connection.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
