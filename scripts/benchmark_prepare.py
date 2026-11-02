"""Pin assets once for the user-authorized full-video notebook campaign."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.benchmarking import atomic_json,file_hash,matrix,caption_segments
from server.catalog import MODEL_CATALOG,installed_models
from server.video_download import download_video,QuietLogger


def prepare(folder,url):
    folder=folder.resolve();folder.mkdir(parents=True,exist_ok=True)
    existing=folder/'campaign.json'
    if existing.exists():
        print('Campaign already prepared; assets preserved.',flush=True)
        return
    atomic_json(folder/'preparation.json',dict(stage='metadata',updated=time.time()))
    import yt_dlp
    import psutil
    from huggingface_hub import HfApi
    from faster_whisper.utils import download_model
    from server.observability import package_versions
    with yt_dlp.YoutubeDL({'quiet':True,'logger':QuietLogger(),'socket_timeout':15,
        'noplaylist':True,'js_runtimes':{'node':{}}}) as ydl:
        info=ydl.extract_info(url,download=False)
        metadata={k:info.get(k) for k in ('id','title','duration','language','availability')}
        atomic_json(folder/'source.json',metadata)
        reference_status='unavailable'
        captions=info.get('automatic_captions',{})
        candidates=captions.get('pt-orig',[]) or captions.get('pt',[])
        candidate=next((v for v in candidates if v.get('ext')=='json3'),None)
        if candidate:
            try:
                with ydl.urlopen(candidate['url']) as response:raw=response.read(8*1024**2+1)
                if len(raw)>8*1024**2:raise ValueError('Caption budget')
                document=json.loads(raw)
                segments=caption_segments(document)
                if not segments:raise ValueError('Empty captions')
                atomic_json(folder/'youtube-automatic.json',segments)
                (folder/'youtube-automatic.txt').write_text(' '.join(s['text'] for s in segments),encoding='utf-8')
                reference_status='youtube_automatic_unverified'
            except Exception as error:
                # Signed caption URLs must not enter logs/artifacts.
                print('Automatic captions unavailable: '+type(error).__name__,flush=True)
    media=folder/'source.media'
    if not media.exists():
        atomic_json(folder/'preparation.json',dict(stage='audio_download',updated=time.time()))
        download_video(url,media)
    digest=file_hash(media)
    probe=subprocess.run([sys.executable,'-m','server.capability_probe'],cwd=ROOT,
        capture_output=True,text=True,encoding='utf-8',timeout=45,check=True)
    hardware=json.loads(probe.stdout)
    cpu=subprocess.run(['powershell','-NoProfile','-Command',
        '(Get-CimInstance Win32_Processor | Select-Object -ExpandProperty Name)'],
        capture_output=True,text=True,timeout=15)
    hardware.update(cpu_name=cpu.stdout.strip(),physical_cpu=psutil.cpu_count(logical=False),
        ram_bytes=psutil.virtual_memory().total,versions=package_versions())
    pinned={}
    installed={m['id']:m for m in installed_models(ROOT/'models')}
    for catalog in MODEL_CATALOG:
        model=catalog['id']
        pin_file=folder/(model+'-weights.json')
        if pin_file.exists():
            pinned[model]=json.loads(pin_file.read_text(encoding='utf-8'));continue
        atomic_json(folder/'preparation.json',dict(stage='model_weights',model=model,updated=time.time()))
        revision=installed[model]['revision'] if installed[model]['installed'] else HfApi().model_info(catalog['repository']).sha
        print('Preparing '+model+' @ '+revision,flush=True)
        path=Path(download_model(catalog['repository'],cache_dir=str(ROOT/'models'),revision=revision))
        pin=dict(model=model,repository=catalog['repository'],revision=revision,path=str(path),
                 model_sha256=file_hash(path/'model.bin'),model_bytes=(path/'model.bin').stat().st_size)
        atomic_json(pin_file,pin);pinned[model]=pin
    plan=matrix(hardware['compute_types'])
    campaign=dict(schema_version=1,id=folder.name,created=time.time(),source=metadata,
        source_url=url,media=str(media),media_sha256=digest,reference=reference_status,
        models=pinned,hardware=hardware,plan=plan,
        options=dict(cpu_threads=8,num_workers=1,beam_size=5,vad_filter=True,
                     language=None,seed=0,temperature=[0,.2,.4,.6,.8,1]),
        trial_timeout_seconds=24*3600,full_video=True,repeat=1)
    atomic_json(existing,campaign)
    atomic_json(folder/'preparation.json',dict(stage='ready',updated=time.time(),combinations=len(plan)))
    print('READY: '+str(len(plan))+' full-video combinations; SHA256 '+digest,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('folder',type=Path);p.add_argument('--url',required=True)
    args=p.parse_args();prepare(args.folder,args.url)
