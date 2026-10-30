"""Provision only the dedicated TTS tunnel, DNS record and Worker.
CF_CREDENTIAL_FILE may point to an existing Bearer token document; never logs it.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key

ROOT=Path(__file__).resolve().parents[1]
ACCOUNT=None
token=None
client=None


def api(method,path,body=None):
    if client is None:
        raise RuntimeError('Cloudflare API not configured')
    r=client.request(method,path,json=body)
    data=r.json()
    if not data.get('success'):
        raise RuntimeError(f'Cloudflare HTTP {r.status_code}; inspect private operator logs')
    return data['result']


def main(argv=None):
    parser=argparse.ArgumentParser(description='Legacy TTS provisioner (read-only plan by default)')
    parser.add_argument('--apply-legacy',action='store_true',
                        help='DANGEROUS: mutate existing TTS tunnel/DNS/Worker after approved backup')
    parser.add_argument('--confirm-legacy-target',default='',
                        help='Must be exactly faysk-tts to permit legacy writes')
    args=parser.parse_args(argv)
    if not args.apply_legacy:
        print(json.dumps({'mode':'plan-only','target':'faysk-tts',
          'legacy_domain':'tts.faysk.dev','legacy_origin':'tts-origin.faysk.dev',
          'writes_performed':False,'next':'Use independent wrangler.stt.jsonc and issue #28'}))
        return
    if args.confirm_legacy_target!='faysk-tts':
        parser.error('Existing TTS resources are protected. Explicit exact legacy target required.')
    global ACCOUNT,token,client
    ACCOUNT=os.environ['CLOUDFLARE_ACCOUNT_ID']
    token=os.getenv('CLOUDFLARE_API_TOKEN')
    if not token:
        text=Path(os.environ['CF_CREDENTIAL_FILE']).read_text(encoding='utf-8-sig')
        tokens=set(re.findall(r'Bearer\s+([A-Za-z0-9_.-]+)',text,re.I))
        if len(tokens)!=1: raise SystemExit('Credencial ausente ou ambígua.')
        token=tokens.pop()
    client=httpx.Client(base_url='https://api.cloudflare.com/client/v4',
                        headers={'Authorization':'Bearer '+token},timeout=60)
    env_path=ROOT/'.env'
    zone=api('GET','/zones?name=faysk.dev')[0]['id']
    tunnels=api('GET',f'/accounts/{ACCOUNT}/cfd_tunnel?name=faysk-tts&is_deleted=false')
    if len(tunnels)>1:
        raise RuntimeError('Multiple tunnels have the same name; refusing mutation')
    tunnel=tunnels[0] if tunnels else api('POST',f'/accounts/{ACCOUNT}/cfd_tunnel',{'name':'faysk-tts','config_src':'cloudflare'})
    tid=tunnel['id']
    state=ROOT/'logs'/'cloudflare-state.json'
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps({'account':ACCOUNT,'zone':zone,'tunnel':tid}),encoding='utf-8')
    desired_ingress=[{'hostname':'tts-origin.faysk.dev','service':'http://127.0.0.1:8765'},
                     {'service':'http_status:404'}]
    if tunnels:
        existing=api('GET',f'/accounts/{ACCOUNT}/cfd_tunnel/{tid}/configurations')
        current=existing.get('config',{}).get('ingress',[])
        # No replacing a shared/multi-host ingress configuration.
        if current != desired_ingress:
            raise RuntimeError('Existing tunnel ingress differs; refusing destructive update')
    else:
        api('PUT',f'/accounts/{ACCOUNT}/cfd_tunnel/{tid}/configurations',
            {'config':{'ingress':desired_ingress}})
    records=api('GET',f'/zones/{zone}/dns_records?name=tts-origin.faysk.dev')
    desired={'type':'CNAME','name':'tts-origin.faysk.dev','content':tid+'.cfargotunnel.com','proxied':True,'ttl':1}
    if len(records)>1: raise RuntimeError('Ambiguous DNS records; refusing mutation')
    if not records: api('POST',f'/zones/{zone}/dns_records',desired)
    elif records[0]['content']!=desired['content']: raise RuntimeError('DNS de origem já existe com outro destino; preservado.')
    tunnel_token=api('GET',f'/accounts/{ACCOUNT}/cfd_tunnel/{tid}/token')
    set_key(env_path,'TTS_TUNNEL_TOKEN',tunnel_token)
    print('Tunnel dedicado e DNS da origem configurados.',flush=True)
    env=os.environ.copy()
    env['CLOUDFLARE_API_TOKEN']=token
    env['CLOUDFLARE_ACCOUNT_ID']=ACCOUNT
    command=['node',str(ROOT/'node_modules/wrangler/bin/wrangler.js')]
    r=subprocess.run(command+['deploy','--var','ORIGIN:https://tts-origin.faysk.dev'],cwd=ROOT,env=env)
    if r.returncode: raise SystemExit(r.returncode)
    secrets={'EDGE_KEY':dotenv_values(env_path)['TTS_EDGE_KEY']}
    subprocess.run(command+['secret','bulk'],input=json.dumps(secrets),text=True,cwd=ROOT,env=env,check=True)
    print('Worker e segredo de origem publicados.',flush=True)

if __name__=='__main__': main()
