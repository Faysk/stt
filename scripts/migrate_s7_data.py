"""One-time migration after public maintenance, preserving browser/passkey state.

Original Windows data is retained. Destination must be the isolated, empty
validation database. This script never starts the tunnel or resumes the queue.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sqlite3
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import urllib.parse
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes',
     '-i',str(Path.home()/'.ssh/s7_lmonitor_ed25519'),'-p','8022','u0_a279@192.168.1.2']
REMOTE=r'''
import hashlib,json,os,shutil,sqlite3,sys,tarfile
from pathlib import Path
os.umask(0o077)
home=Path.home();root=home/'storage/external-1/stt-api'
if not (root/'.stt-managed').is_file() or root.stat().st_dev==home.stat().st_dev:raise SystemExit('INVALID_DATA_VOLUME')
if shutil.disk_usage(home).free<2*1024**3:raise SystemExit('INTERNAL_RESERVE_UNAVAILABLE')
incoming=root/'incoming-20261101'
if incoming.exists() or (root/'.migrated-20261101').exists():raise SystemExit('MIGRATION_ALREADY_STAGED')
incoming.mkdir()
total=0
with tarfile.open(fileobj=sys.stdin.buffer,mode='r|') as archive:
 for member in archive:
  rel=Path(member.name)
  if not member.isfile() or rel.is_absolute() or '..' in rel.parts or rel.parts[0] not in ('db','blob'):raise SystemExit('UNSAFE_ARCHIVE_ENTRY')
  total+=member.size
  if total>512*1024**2:raise SystemExit('MIGRATION_SIZE_LIMIT')
  path=incoming/rel
  path.parent.mkdir(parents=True,exist_ok=True)
  with archive.extractfile(member) as src,path.open('xb') as dst:shutil.copyfileobj(src,dst)
dbpath=incoming/'db/stt.sqlite3'
with sqlite3.connect(dbpath) as db:
 if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('DATABASE_INTEGRITY_FAILED')
 jobs=db.execute('SELECT count(*) FROM jobs').fetchone()[0]
 if not json.loads(db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])['paused']:raise SystemExit('SNAPSHOT_NOT_PAUSED')
 db.execute('UPDATE workers SET seen=0')
 db.commit()
current=root/'data'
with sqlite3.connect(current/'db/stt.sqlite3') as db:
 if db.execute('SELECT count(*) FROM jobs').fetchone()[0]:raise SystemExit('DESTINATION_HAS_USER_JOBS')
for path in (current,incoming,root/'validation-before-migration'):
 if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():raise SystemExit('INVALID_RENAME_TARGET')
current.rename(root/'validation-before-migration');incoming.rename(current)
(root/'.migrated-20261101').write_text('1')
print(json.dumps({'migrated_jobs':jobs,'transferred_bytes':total,'integrity':'ok','original_windows_data_preserved':True}))
'''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def open_checked(request, timeout):
    """Refuse redirects and all hosts other than the two known STT endpoints."""
    parsed = urllib.parse.urlsplit(request.full_url)
    local = (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
             and parsed.port == 8765
             and parsed.path in ('/api/admin', '/api/admin/settings'))
    public = (parsed.scheme == 'https' and parsed.hostname == 'stt.faysk.dev'
              and parsed.port is None and parsed.path == '/api/health')
    if not (local or public) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('UNTRUSTED_MIGRATION_ENDPOINT')
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def main(apply=False):
    values=dotenv_values(ROOT/'.env')
    data=Path(values.get('STT_DATA') or ROOT).resolve()
    headers={'x-admin-key':values['STT_ADMIN_KEY'],'x-edge-key':values['STT_EDGE_KEY'],'content-type':'application/json'}
    url='http://127.0.0.1:8765/api/admin'
    with open_checked(urllib.request.Request(url,headers=headers),timeout=15) as response:
        state=json.load(response)
    if any(job['status'] in ('running','uploading') for job in state['jobs']):
        raise SystemExit('DRAIN_ACTIVE_JOBS_FIRST')
    print(json.dumps({'preview':not apply,'queued':state['counts'].get('queued',0)}),flush=True)
    if not apply:return
    try:
        open_checked(urllib.request.Request('https://stt.faysk.dev/api/health',headers={'User-Agent':'Mozilla/5.0'}),timeout=15)
        raise SystemExit('PUBLIC_MAINTENANCE_REQUIRED')
    except urllib.error.HTTPError as error:
        if error.code!=503:raise SystemExit('PUBLIC_MAINTENANCE_NOT_CONFIRMED')
    backup=ROOT/'logs/s7-migration-20261101'
    backup.mkdir(exist_ok=False)
    (backup/'settings-before.json').write_text(json.dumps(state['settings']))
    settings=dict(state['settings'],paused=True)
    open_checked(urllib.request.Request(url+'/settings',headers=headers,method='PUT',data=json.dumps(settings).encode()),timeout=15).close()
    source=data/'db/stt.sqlite3'
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(backup/'stt.sqlite3') as dst:
        if src.execute('SELECT count(*) FROM upload_slots').fetchone()[0]:raise SystemExit('UPLOADS_STILL_IN_FLIGHT')
        src.backup(dst)
    archive=backup/'data.tar'
    with sqlite3.connect(backup/'stt.sqlite3') as connection:
        live=[row[0] for row in connection.execute('SELECT id FROM jobs WHERE expires>? AND purged_at IS NULL',(time.time(),))]
    with tarfile.open(archive,'w') as output:
        output.add(backup/'stt.sqlite3',arcname='db/stt.sqlite3',recursive=False)
        for job in live:
            folder=data/'blob'/job
            if not folder.is_dir():continue
            for path in folder.rglob('*'):
                if path.is_symlink():raise SystemExit('SYMLINK_IN_SOURCE_REFUSED')
                if path.is_file():output.add(path,arcname=(Path('blob')/job/path.relative_to(folder)).as_posix(),recursive=False)
    subprocess.run(SSH+['sv down ~/.local/share/stt-services/api'],check=True)
    with archive.open('rb') as stream:
        subprocess.run(SSH+['python -c '+shlex.quote(REMOTE)],stdin=stream,check=True,timeout=120)
    subprocess.run(SSH+['sv up ~/.local/share/stt-services/api'],check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    main(parser.parse_args().apply)
