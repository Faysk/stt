"""Install API source/services over verified SSH. Does not migrate data or route traffic."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
REMOTE=r'''
import base64,json,os,shutil
from pathlib import Path
payload=PAYLOAD
home=Path.home();external=home/'storage/external-1'
if not external.is_dir() or external.stat().st_dev==home.stat().st_dev:raise SystemExit('SD_UNAVAILABLE')
if shutil.disk_usage(home).free<2*1024**3+64*1024**2:raise SystemExit('INTERNAL_RESERVE_UNAVAILABLE')
canonical=external/'stt-api';private=home/'.local/share/stt-api';runtime=private/'runtime'
services=home/'.local/share/stt-services'
for path in (canonical,private,services):
 if path.is_symlink():raise SystemExit('SYMLINK_REFUSED')
if canonical.exists() and not (canonical/'.stt-managed').exists():raise SystemExit('UNMANAGED_SD_DIRECTORY')
if private.exists() and not (private/'.stt-managed').exists():raise SystemExit('UNMANAGED_RUNTIME')
if not (services/'.stt-managed').is_file():raise SystemExit('STT_SUPERVISOR_REQUIRED')
if not payload['apply']:
 print(json.dumps({'preview':True,'sd_available':True,'internal_free':shutil.disk_usage(home).free}));raise SystemExit(0)
os.umask(0o077)
canonical.mkdir(exist_ok=True);private.mkdir(parents=True,exist_ok=True)
(canonical/'.stt-managed').write_text('1');(private/'.stt-managed').write_text('1')
for name,encoded in payload['files'].items():
 rel=Path(name)
 if rel.is_absolute() or '..' in rel.parts:raise SystemExit('INVALID_SOURCE_PATH')
 for root in (canonical/'source',runtime):
  destination=root/rel
  if destination.is_symlink():raise SystemExit('SYMLINK_REFUSED')
  destination.parent.mkdir(parents=True,exist_ok=True)
  destination.write_bytes(base64.b64decode(encoded))
data=canonical/'data';(data/'db').mkdir(parents=True,exist_ok=True);(data/'blob').mkdir(exist_ok=True)
values=payload['settings']
values.update(STT_DATA=str(data.resolve()),STT_MODELS=str(canonical/'models'),STT_LIGHT_HOST='1',
 STT_BLOB_BUDGET_MB='2048',STT_REQUIRE_EDGE='1',STT_S7_ENABLED='1',STT_S7_SIDE_WORKER='1',
 STT_S7_SSH_HOST='192.168.1.2',STT_S7_SSH_USER='u0_a279',STT_S7_SSH_PORT='8022')
for value in values.values():
 if not isinstance(value,str) or any(char in value for char in '\n\r\x00'):raise SystemExit('INVALID_ENV_VALUE')
env=runtime/'.env'
env.write_text(''.join(key+'='+json.dumps(value)+'\n' for key,value in values.items()))
env.chmod(0o600)
prefix='/data/data/com.termux/files/usr'
def write(path,value):
 if path.is_symlink():raise SystemExit('SYMLINK_REFUSED')
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value);path.chmod(0o700)
api='#!'+prefix+'/bin/sh\nset -eu\nexec 2>&1\numask 077\ncd "'+str(runtime)+'"\nexec "'+str(home/'.local/share/stt-api-venv/bin/python')+'" -m uvicorn server.app:app --host 127.0.0.1 --port 8786 --no-access-log\n'
tunnel='#!'+prefix+'/bin/sh\nset -eu\nexec 2>&1\nexec "'+prefix+'/bin/python" "'+str(private/'tunnel-launch.py')+'"\n'
launcher="import json,os\nfrom pathlib import Path\np=Path.home()/'.config/stt-api/tunnel.json'\nos.environ['TUNNEL_TOKEN']=json.loads(p.read_text())['token']\nos.execvp('cloudflared',['cloudflared','tunnel','--no-autoupdate','--metrics','127.0.0.1:49322','run'])\n"
write(private/'tunnel-launch.py',launcher)
for role,command in [('api',api),('tunnel',tunnel)]:
 service=services/role
 if not service.exists() and role=='tunnel':
  service.mkdir();(service/'down').write_text('')
 write(service/'run',command)
 write(service/'finish','#!'+prefix+'/bin/sh\nexec 2>&1\nprintf "STT_SERVICE_EXIT code=%s signal=%s\\n" "$1" "$2"\nsleep 15\n')
 log=canonical/'logs'/role;log.mkdir(parents=True,exist_ok=True)
 (log/'config').write_text('s65536\nn4\n')
 write(service/'log/run','#!'+prefix+'/bin/sh\nexec '+prefix+'/bin/svlogd "'+str(log)+'"\n')
print(json.dumps({'installed':True,'api_port':8786,'tunnel_auto_start':not (services/'tunnel/down').exists(),'public_route_changed':False}))
'''


def main(apply=False):
    allowed=('STT_ADMIN_KEY','STT_WORKER_KEY','STT_S7_WORKER_KEY','STT_EDGE_KEY',
             'STT_ADMIN_QR_ENABLED','STT_ADMIN_LEGACY_ENABLED')
    values=dotenv_values(ROOT/'.env')
    settings={key:values[key] for key in allowed if values.get(key) is not None}
    for key in allowed[:4]:
        if not settings.get(key):raise SystemExit('REQUIRED_API_SECRET_MISSING')
    files={}
    for folder in ('server','web'):
        for path in (ROOT/folder).iterdir():
            if path.is_file() and path.suffix in ('.py','.html','.js','.css','.svg','.png','.txt','.xml','.webmanifest'):
                content=path.read_bytes()
                if path.suffix!='.png':content=content.replace(b'\r\n',b'\n')
                files[path.relative_to(ROOT).as_posix()]=base64.b64encode(content).decode()
    body=REMOTE.replace('PAYLOAD',repr(dict(apply=apply,files=files,settings=settings)),1)
    subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes',
        '-i',str(Path.home()/'.ssh/s7_lmonitor_ed25519'),'-p','8022','u0_a279@192.168.1.2','python -'],
        input=body,text=True,encoding='utf-8',check=True,timeout=60)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    main(parser.parse_args().apply)
