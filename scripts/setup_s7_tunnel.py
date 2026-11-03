"""Provision an S7 connector for STT; cutover only after the local relay is ready."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tomllib
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
HOSTNAME = 'stt-origin.faysk.dev'
NAME = 'faysk-stt'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def open_checked(request, timeout):
    """Provider API and local S7 gate only; reject redirects and foreign origins."""
    parsed = urllib.parse.urlsplit(request.full_url)
    provider = (parsed.scheme == 'https' and parsed.hostname == 'api.cloudflare.com'
                and parsed.port is None and parsed.path.startswith('/client/v4/'))
    local = (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
             and parsed.port == 8786 and parsed.path == '/api/health')
    if not (provider or local) or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('UNTRUSTED_TUNNEL_PROVISION_ENDPOINT')
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def main(apply=False, cutover=False, maintenance=False):
    candidates = [Path(os.environ['APPDATA'])/'xdg.config/.wrangler/config/default.toml',
                  Path.home()/'.wrangler/config/default.toml']
    config = next((path for path in candidates if path.is_file()), None)
    if config is None:
        raise SystemExit('WRANGLER_AUTH_NOT_FOUND')
    auth = tomllib.loads(config.read_text(encoding='utf-8'))
    token = auth['oauth_token']

    def api(path, method='GET', value=None):
        request = urllib.request.Request('https://api.cloudflare.com/client/v4'+path,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},
            method=method,data=None if value is None else json.dumps(value).encode())
        with open_checked(request,timeout=30) as response:
            result = json.load(response)
        if not result.get('success'):
            raise SystemExit('CLOUDFLARE_API_REQUEST_FAILED')
        return result['result']

    zones = api('/zones?name=faysk.dev')
    if len(zones)!=1:
        raise SystemExit('EXPECTED_SINGLE_ZONE')
    zone = zones[0]
    account = zone['account']['id']
    tunnels = api('/accounts/'+account+'/cfd_tunnel?name='+NAME+'&is_deleted=false')
    if len(tunnels)!=1 or tunnels[0]['name']!=NAME:
        raise SystemExit('EXPECTED_DEDICATED_S7_TUNNEL')
    tunnel = tunnels[0]['id']
    endpoint = '/accounts/'+account+'/cfd_tunnel/'+tunnel
    configuration = api(endpoint+'/configurations')['config']
    ingress = configuration.get('ingress',[])
    if len(ingress)!=2 or ingress[0].get('hostname')!=HOSTNAME or ingress[1].get('service')!='http_status:404':
        raise SystemExit('UNEXPECTED_SHARED_TUNNEL_CONFIGURATION')
    if ingress[0].get('service') not in ('http://127.0.0.1:8765','http://127.0.0.1:8786','http_status:503'):
        raise SystemExit('UNEXPECTED_ORIGIN_SERVICE')
    print(json.dumps({'preview':not apply,'tunnel':NAME,'hostname':HOSTNAME,'cutover':cutover,'dns_changed':False}),flush=True)
    if not apply:
        return
    backup=ROOT/'logs/s7-origin-config-before.json'
    if not backup.exists():backup.write_text(json.dumps(configuration))
    if maintenance:
        ingress[0]['service']='http_status:503'
        api(endpoint+'/configurations','PUT',{'config':configuration})
    if cutover:
        from dotenv import dotenv_values
        secret=dotenv_values(ROOT/'.env')['STT_EDGE_KEY']
        request=urllib.request.Request('http://127.0.0.1:8786/api/health',headers={'x-edge-key':secret})
        with open_checked(request,timeout=15) as response:
            health=json.load(response)
        if health.get('max_mb')!=8 or health.get('formats')!=['wav','mp3','ogg','m4a'] or health.get('max_seconds')!=300:
            raise SystemExit('S7_API_HEALTH_GATE_FAILED')
        ingress[0]['service']='http://127.0.0.1:8786'
        api(endpoint+'/configurations','PUT',{'config':configuration})
    scoped_token = api(endpoint+'/token')
    # Do not persist the account-wide OAuth token or connector token locally.
    remote = '''import json,os
from pathlib import Path
os.umask(0o077)
folder=Path.home()/'.config/stt-api'
folder.mkdir(parents=True,exist_ok=True)
if folder.is_symlink():raise SystemExit('SYMLINK_REFUSED')
path=folder/'tunnel.json'
if path.is_symlink():raise SystemExit('SYMLINK_REFUSED')
path.write_text(json.dumps({'token':TOKEN}))
path.chmod(0o600)
print('SCOPED_S7_TUNNEL_TOKEN_INSTALLED')
'''.replace('TOKEN',repr(scoped_token),1)
    subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes',
        '-i',str(Path.home()/'.ssh/s7_lmonitor_ed25519'),'-p','8022','u0_a279@192.168.1.2','python -'],
        input=remote,text=True,encoding='utf-8',check=True,timeout=30)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--cutover',action='store_true')
    parser.add_argument('--maintenance',action='store_true')
    args=parser.parse_args()
    if args.cutover and not args.apply:parser.error('--cutover requires --apply')
    if args.maintenance and (not args.apply or args.cutover):parser.error('--maintenance requires --apply and excludes --cutover')
    main(args.apply,args.cutover,args.maintenance)
