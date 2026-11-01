"""Provision only the STT runit tree over verified SSH; no keys or other services changed."""
import argparse
import base64
import json
from pathlib import Path
import subprocess


REMOTE = r'''
import base64,json,os,shutil,subprocess,time
from pathlib import Path
payload=PAYLOAD
home=Path.home().resolve()
external=home/'storage/external-1'
if not external.is_dir() or external.stat().st_dev==home.stat().st_dev:
    raise SystemExit('SD_UNAVAILABLE')
if shutil.disk_usage(home).free < 2*1024**3+16*1024**2:
    raise SystemExit('INTERNAL_RESERVE_UNAVAILABLE')
if shutil.disk_usage(external).free < 4*1024**2:
    raise SystemExit('SD_RESERVE_UNAVAILABLE')
prefix=Path('/data/data/com.termux/files/usr')
if not all((prefix/'bin'/n).is_file() for n in ('python','runsvdir','sv','svlogd','termux-wake-lock')):
    raise SystemExit('SERVICE_DEPENDENCY_MISSING')
app=external/'stt-worker'
services=home/'.local/share/stt-services'
boot=home/'.termux/boot/20-stt-worker'
runtime=home/'stt-lab/stt-worker.py'
pending=list((home/'stt-lab/stt-worker').glob('result-*.json'))
if payload['apply'] and pending:
    raise SystemExit('DRAIN_LEGACY_OUTBOX_FIRST')
for p in (app,services,boot,runtime):
    if p.is_symlink():raise SystemExit('SYMLINK_TARGET_REFUSED')
for p in (services,boot,runtime):
    if not p.parent.resolve().is_relative_to(home):
        raise SystemExit('INVALID_PRIVATE_PARENT')
if app.exists() and not (app/'.stt-managed').is_file():
    raise SystemExit('UNMANAGED_SD_DIRECTORY')
if services.exists() and not (services/'.stt-managed').is_file():
    raise SystemExit('UNMANAGED_SERVICE_DIRECTORY')
if not payload['apply']:
    print(json.dumps({'preview':True,'internal_free':shutil.disk_usage(home).free,
      'sd_available':True,'service_exists':services.exists()}))
    raise SystemExit(0)
os.umask(0o077)
app.mkdir(exist_ok=True)
services.mkdir(parents=True,exist_ok=True,mode=0o700)
if not services.resolve().is_relative_to(home):raise SystemExit('INVALID_PRIVATE_SERVICE_ROOT')
def write(path,data,mode):
    if path.is_symlink():raise SystemExit('SYMLINK_FILE_REFUSED')
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.stt-new-'+str(os.getpid()))
    with tmp.open('xb') as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    tmp.chmod(mode);tmp.replace(path)
sources={k:base64.b64decode(v) for k,v in payload['files'].items()}
write(app/'.stt-managed',b'STT service v1\n',0o600)
write(services/'.stt-managed',b'STT service v1\n',0o600)
for name,source in sources.items():
    write(app/name,source,0o600)
    if name.endswith('.sh'):
        subprocess.run([str(prefix/'bin/sh'),'-n',str(app/name)],check=True)
if runtime.exists():write(app/'worker.previous.py',runtime.read_bytes(),0o600)
write(runtime,sources['worker.py'],0o600)
write(services/'worker/run',sources['service-run.sh'],0o700)
write(services/'start-service.sh',sources['start-service.sh'],0o700)
write(boot,sources['boot-service.sh'],0o700)
log=app/'logs'
if log.is_symlink() or not log.resolve().is_relative_to(app.resolve()):
    raise SystemExit('INVALID_LOG_DIRECTORY')
log.mkdir(exist_ok=True)
write(log/'config',b's65536\nn4\n',0o600)
logger=('#!'+str(prefix/'bin/sh')+'\nexec '+str(prefix/'bin/svlogd')+' "'+str(log)+'"\n').encode()
write(services/'worker/log/run',logger,0o700)
write(services/'worker/finish',('#!'+str(prefix/'bin/sh')+'\nsleep 15\nexit 0\n').encode(),0o700)
if payload['start']:
    subprocess.run([str(prefix/'bin/sh'),str(services/'start-service.sh')],check=True)
print(json.dumps({'installed':True,'started':payload['start'],'private_runtime':True,
  'sd_code_and_logs':True,'other_services_changed':False}))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True)
    parser.add_argument('--user',required=True)
    parser.add_argument('--port',type=int,default=8022)
    parser.add_argument('--identity',type=Path,required=True)
    parser.add_argument('--host-key-alias')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--start',action='store_true')
    args=parser.parse_args()
    if args.start and not args.apply:parser.error('--start requires --apply')
    root=Path(__file__).resolve().parents[1]
    files={name:base64.b64encode((root/'s7'/name).read_bytes().replace(b'\r\n',b'\n')).decode()
           for name in ('worker.py','service-run.sh','start-service.sh','boot-service.sh')}
    body=REMOTE.replace('PAYLOAD',repr(dict(files=files,apply=args.apply,start=args.start)),1)
    command=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
             '-o','IdentitiesOnly=yes','-i',str(args.identity),'-p',str(args.port)]
    if args.host_key_alias:command+=['-o','HostKeyAlias='+args.host_key_alias]
    command += [args.user+'@'+args.host,'python -']
    result=subprocess.run(command,input=body,text=True,encoding='utf-8',timeout=45,check=False)
    raise SystemExit(result.returncode)


if __name__=='__main__':main()
