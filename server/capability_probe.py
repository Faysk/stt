"""Short-lived inventory process: CUDA probing must not retain worker VRAM."""
import json,os,platform,subprocess
from pathlib import Path
from server.worker import ROOT,_qwen_models
from server.config import getenv
from server.observability import package_versions

def probe():
    import psutil
    caps={'system':platform.system(),'machine':platform.machine(),
          'ram_total_mb':round(psutil.virtual_memory().total/1024**2),
          'cpu':os.cpu_count(),'engine':'faster-whisper',
          'qwen_models':_qwen_models(),'versions':package_versions(),
          'compute_types':{'cpu':[],'cuda':[]},'models':[]}
    try:
        import ctranslate2
        caps['compute_types']['cpu']=sorted(ctranslate2.get_supported_compute_types('cpu'))
    except (ImportError,RuntimeError,ValueError,OSError):
        pass
    try:
        flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        response=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,memory.free','--format=csv,noheader,nounits'],
                                capture_output=True,text=True,timeout=3,**flags)
        if response.returncode==0 and response.stdout.strip():
            fields=[part.strip() for part in response.stdout.splitlines()[0].split(',')]
            try:
                available_vram=int(float(fields[2]))
            except (ValueError,IndexError):
                available_vram=None
            caps['gpu']={'name':fields[0][:120],'vram_mb':int(float(fields[1])),
                         'free_vram_mb':available_vram}
            try:
                import ctranslate2
                caps['compute_types']['cuda']=sorted(ctranslate2.get_supported_compute_types('cuda'))
            except (ImportError,RuntimeError,ValueError,OSError):
                pass
    except (OSError,ValueError,IndexError,subprocess.TimeoutExpired):
        pass
    try:
        from server.catalog import installed_models
        caps['models']=[{'id':item['id'],'revision':item['revision']}
            for item in installed_models(getenv('STT_MODELS',str(ROOT/'models'))) if item['installed']]
    except (OSError,ValueError):
        pass
    return caps

if __name__=="__main__": print(json.dumps(probe()))
