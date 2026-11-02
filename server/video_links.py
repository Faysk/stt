"""Single-video social links; no arbitrary URLs, profiles or generic extraction."""
import re
from typing import NamedTuple
from urllib.parse import parse_qs, urlparse, urljoin
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
from urllib.error import HTTPError


class VideoLink(NamedTuple):
    url: str
    platform: str
    short: bool = False


class VideoLinkError(ValueError):
    pass


def video_link(value):
    if not isinstance(value,str) or len(value)>2048:
        raise VideoLinkError('Invalid link')
    value=value.strip()
    if any(ord(char)<33 or ord(char)==127 for char in value) or '\\' in value:
        raise VideoLinkError('Invalid link')
    if '://' not in value:value='https://'+value
    try:
        u=urlparse(value)
        if u.scheme!='https' or u.username or u.password or u.port not in (None,443):
            raise VideoLinkError('HTTPS video link required')
        host=u.hostname
    except ValueError as error:
        raise VideoLinkError('Invalid link') from error
    path=u.path.rstrip('/')
    query=parse_qs(u.query)
    if host in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be'):
        vid=query.get('v',[''])[0] if path=='/watch' else ''
        if host=='youtu.be':vid=path.lstrip('/')
        elif re.fullmatch(r'/(shorts|live)/[\w-]{11}',path,flags=re.ASCII):vid=path.split('/')[-1]
        if re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):
            return VideoLink('https://www.youtube.com/watch?v='+vid,'YouTube')
    elif host in ('instagram.com','www.instagram.com','m.instagram.com'):
        match=re.fullmatch(r'/(?:p|tv|reels?)/([A-Za-z0-9_-]{5,64})',path)
        if match:return VideoLink('https://www.instagram.com/reel/'+match[1]+'/' if path.startswith(('/reel/','/reels/')) else 'https://www.instagram.com'+path+'/', 'Instagram')
    elif host in ('tiktok.com','www.tiktok.com','m.tiktok.com'):
        if re.fullmatch(r'/@[A-Za-z0-9_.-]+/video/[0-9]{5,30}',path):
            return VideoLink('https://www.tiktok.com'+path,'TikTok')
        if re.fullmatch(r'/t/[A-Za-z0-9]{5,64}',path):
            return VideoLink('https://www.tiktok.com'+path+'/','TikTok',True)
    elif host in ('vm.tiktok.com','vt.tiktok.com'):
        if re.fullmatch(r'/[A-Za-z0-9]{5,64}',path):return VideoLink('https://'+host+path+'/','TikTok',True)
    elif host in ('facebook.com','www.facebook.com','m.facebook.com','web.facebook.com','fb.watch','www.fb.watch'):
        if host in ('fb.watch','www.fb.watch') and re.fullmatch(r'/[A-Za-z0-9_-]{3,80}',path):
            return VideoLink('https://fb.watch'+path+'/','Facebook',True)
        if host not in ('fb.watch','www.fb.watch'):
            vid=query.get('v',[''])[0]
            if path in ('/watch','/video.php','/video/video.php') and re.fullmatch(r'[0-9]{5,30}',vid):
                return VideoLink('https://www.facebook.com/watch/?v='+vid,'Facebook')
            if re.fullmatch(r'/reel/[0-9]{5,30}',path) or re.fullmatch(r'/[A-Za-z0-9_.-]+/videos/(?:[A-Za-z0-9_-]+/)?[0-9]{5,30}',path):
                return VideoLink('https://www.facebook.com'+path+'/','Facebook')
            if re.fullmatch(r'/share/(?:v|r)/[A-Za-z0-9_-]{3,100}',path):
                return VideoLink('https://www.facebook.com'+path+'/','Facebook',True)
    elif host in ('x.com','www.x.com','m.x.com','mobile.x.com','twitter.com','www.twitter.com','m.twitter.com','mobile.twitter.com'):
        match=re.fullmatch(r'/(?:[A-Za-z0-9_]{1,30}|i/web)/status/([0-9]{5,30})(/video/[1-9][0-9]?)?',path)
        if match:return VideoLink('https://x.com/i/web/status/'+match[1]+(match[2] or ''),'X')
    raise VideoLinkError('Unsupported video link')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def resolve_video_link(value):
    """Resolve only platform-owned share links; validate every redirect first."""
    link=video_link(value)
    platform=link.platform
    opener=build_opener(ProxyHandler({}),NoRedirect())
    for _ in range(5):
        if not link.short:return link
        try:
            response=opener.open(Request(link.url,headers={'User-Agent':'Mozilla/5.0'}),timeout=10)
        except HTTPError as error:response=error
        try:
            if response.status not in (301,302,303,307,308):raise VideoLinkError('Share link unavailable')
            target=response.headers.get('Location')
            if not target:raise VideoLinkError('Share link unavailable')
            link=video_link(urljoin(link.url,target))
            if link.platform!=platform:raise VideoLinkError('Unexpected share redirect')
        finally:response.close()
    if not link.short:return link
    raise VideoLinkError('Share redirect budget exceeded')
