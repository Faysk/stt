"""Repeat isolated local inference without changing the public queue settings."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

def wer(reference,hypothesis):
    import re
    a=re.findall(r'\w+',reference.lower())
    b=re.findall(r'\w+',hypothesis.lower())
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        row=[i]
        for j,y in enumerate(b,1): row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=row
    return previous[-1]/max(len(a),1)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('media',type=Path)
    p.add_argument('--model',default='small')
    p.add_argument('--repeat',type=int,default=3)
    p.add_argument('--reference',type=Path)
    p.add_argument('--out',type=Path,default=Path('logs/benchmark.json'))
    args=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    temp_root=root/'blob'/'.tmp'
    temp_root.mkdir(parents=True,exist_ok=True)
    report=[]
    for device,compute in [('cpu','int8'),('cuda','float16')]:
        for trial in range(args.repeat):
            with tempfile.TemporaryDirectory(dir=temp_root,prefix='benchmark-') as temp:
                folder=Path(temp)
                shutil.copyfile(args.media,folder/'input.media')
                (folder/'job.json').write_text(json.dumps(dict(model=args.model,source='upload',device=device,compute=compute)),encoding='utf-8')
                subprocess.run([sys.executable,'-m','server.infer',str(folder/'job.json')],cwd=root,timeout=7200,check=True)
                data=json.loads((folder/'result.json').read_text(encoding='utf-8'))
                text=' '.join(s['text'] for s in data.pop('segments',[]))
                if args.reference: data['wer']=wer(args.reference.read_text(encoding='utf-8'),text)
                data.update(model=args.model,device=device,compute=compute,trial=trial+1)
                if data.get('duration'): data['rtf']=data['elapsed']/data['duration']
                report.append(data)
                args.out.parent.mkdir(parents=True,exist_ok=True)
                args.out.write_text(json.dumps(report,indent=2),encoding='utf-8')
                print(json.dumps(data),flush=True)

if __name__=='__main__': main()
