"""Bounded public-video download shared by Whisper and isolated Qwen runtime."""
import time
from server.video_links import resolve_video_link

MAX_BYTES=90*1024**2
MAX_SECONDS=7200
DOWNLOAD_SECONDS=600
EXTRACTORS=['youtube$', 'instagram$', 'tiktok$', 'vm\\.tiktok$', 'facebook$', 'facebook:reel$', 'twitter$']


class VideoDownloadError(Exception):
    def __init__(self,code='LINK_UNAVAILABLE'):
        self.code=code
        super().__init__(code)


class QuietLogger:
    # yt-dlp messages can contain signed media URLs; never put them in logs.
    def debug(self,*args,**kwargs):pass
    def warning(self,*args,**kwargs):pass
    def error(self,*args,**kwargs):pass


def download_video(source,destination,progress=None):
    started=time.monotonic()
    def budget(info):
        if info.get('downloaded_bytes',0)>MAX_BYTES or time.monotonic()-started>DOWNLOAD_SECONDS:
            raise VideoDownloadError('LINK_LIMIT')
        if progress:
            received=info.get('downloaded_bytes',0)
            total=info.get('total_bytes') or info.get('total_bytes_estimate')
            progress(min(99,received/total*100) if total else 0)
    def match(info,**kwargs):
        if info.get('is_live') or info.get('live_status') in ('is_live','is_upcoming'):
            raise VideoDownloadError('LINK_UNAVAILABLE')
        if (info.get('duration') or 0)>MAX_SECONDS or (info.get('filesize') or info.get('filesize_approx') or 0)>MAX_BYTES:
            raise VideoDownloadError('LINK_LIMIT')
        formats=info.get('formats')
        if formats and all(item.get('acodec')=='none' for item in formats):
            raise VideoDownloadError('LINK_NO_AUDIO')
        return None
    try:
        import yt_dlp
        link=resolve_video_link(source)
        with yt_dlp.YoutubeDL({'format':'bestaudio/best','outtmpl':str(destination),
            'noplaylist':True,'playlist_items':'1',
            'allowed_extractors':EXTRACTORS,'max_filesize':MAX_BYTES,
            'socket_timeout':15,'retries':2,'fragment_retries':2,'extractor_retries':1,
            'concurrent_fragment_downloads':1,'skip_unavailable_fragments':False,
            'quiet':True,'no_warnings':True,'logger':QuietLogger(),
            'js_runtimes':{'node':{}},'progress_hooks':[budget],'match_filter':match}) as ydl:
            info=ydl.extract_info(link.url,download=True)
        if isinstance(info,dict) and info.get('_type') in ('playlist','multi_video'):
            info=next((item for item in info.get('entries',[]) if item),None)
        if not isinstance(info,dict) or not destination.is_file() or not destination.stat().st_size:
            raise VideoDownloadError()
        if destination.stat().st_size>MAX_BYTES:raise VideoDownloadError('LINK_LIMIT')
        if info.get('acodec')=='none':raise VideoDownloadError('LINK_NO_AUDIO')
        return {'title':str(info.get('title') or link.platform)[:180],'platform':link.platform}
    except VideoDownloadError:raise
    except Exception as error:
        raise VideoDownloadError() from error
