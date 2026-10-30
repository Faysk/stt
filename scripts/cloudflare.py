"""Provision only the dedicated TTS tunnel, DNS record and Worker.
CF_CREDENTIAL_FILE may point to an existing Bearer token document; never logs it.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key

ROOT=Path(__file__).resolve().parents[1]
ACCOUNT=os.environ['CLOUDFLARE_ACCOUNT_ID']
token=os.getenv('CLOUDFLARE_API_TOKEN')
if not token:
    text=Path(os.environ['CF_CREDENTIAL_FILE']).read_text(encoding='utf-8-sig')
    tokens=set(re.findall(r'Bearer\s+([A-Za-z0-9_.-]+)',text,re.I))
    if len(tokens)!=1: raise SystemExit('Credencial ausente ou ambígua.')
    token=tokens.pop()
client=httpx.Client(base_url='https://api.cloudflare.com/client/v4',headers={'Authorization':'Bearer '+token},timeout=60)
def api(method,path,body=None):
    r=client.request(method,path,json=body)
    data=r.json()
    if not data.get('success'):
        raise RuntimeError(f'Cloudflare {r.status_code}: '+json.dumps(data.get('errors')))
    return data['result']

def main():
    env_path=ROOT/'.env'
    zone=api('GET','/zones?name=faysk.dev')[0]['id']
    tunnels=api('GET',f'/accounts/{ACCOUNT}/cfd_tunnel?name=faysk-tts&is_deleted=false')
    tunnel=tunnels[0] if tunnels else api('POST',f'/accounts/{ACCOUNT}/cfd_tunnel',{'name':'faysk-tts','config_src':'cloudflare'})
    tid=tunnel['id']
    state=ROOT/'logs'/'cloudflare-state.json'
    state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps({'account':ACCOUNT,'zone':zone,'tunnel':tid}),encoding='utf-8')
    api('PUT',f'/accounts/{ACCOUNT}/cfd_tunnel/{tid}/configurations',{'config':{'ingress':[{'hostname':'tts-origin.faysk.dev','service':'http://127.0.0.1:8765'},{'service':'http_status:404'}]}})
    records=api('GET',f'/zones/{zone}/dns_records?name=tts-origin.faysk.dev')
    desired={'type':'CNAME','name':'tts-origin.faysk.dev','content':tid+'.cfargotunnel.com','proxied':True,'ttl':1}
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
