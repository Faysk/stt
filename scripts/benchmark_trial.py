"""One fresh full-video inference process, with independent telemetry artifacts."""
import csv
import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.benchmarking import atomic_json,caption_agreement,words


def gpu_summary(path,start):
    points=[]
    try:
        for row in csv.reader(path.read_text(encoding='utf-8').splitlines()):
            try:
                at=datetime.datetime.strptime(row[0].strip(),'%Y/%m/%d %H:%M:%S.%f').timestamp()
                values=[float(v.strip()) if v.strip() not in ('N/A','[N/A]','Not Supported') else None for v in row[1:]]
                points.append((at,*values))
            except (ValueError,IndexError):continue
    except OSError:pass
    active=[p for p in points if p[0]>=start]
    baseline=[p for p in points if p[0]<start]
    def avg(rows,index):
        vals=[r[index] for r in rows if len(r)>index and r[index] is not None]
        return sum(vals)/len(vals) if vals else None
    def peak(rows,index):
        vals=[r[index] for r in rows if len(r)>index and r[index] is not None]
        return max(vals) if vals else None
    energy=0;coverage=0
    for a,b in zip(active,active[1:]):
        dt=b[0]-a[0]
        if 0<dt<=5 and a[5] is not None and b[5] is not None:
            energy+=(a[5]+b[5])/2*dt/3600;coverage+=dt
    return dict(gpu_host_samples=len(active),gpu_host_utilization_mean=avg(active,1),
        gpu_host_used_mb_peak=peak(active,2),gpu_temperature_c_peak=peak(active,4),
        gpu_board_power_w_mean=avg(active,5),gpu_board_power_w_peak=peak(active,5),
        gpu_board_energy_wh=energy if coverage else None,gpu_energy_coverage_s=coverage,
        gpu_sm_clock_mhz_mean=avg(active,6),gpu_baseline_used_mb=avg(baseline,2),
        gpu_baseline_temperature_c=avg(baseline,4),gpu_baseline_power_w=avg(baseline,5),
        energy_scope='whole_gpu_board_including_other_processes',cpu_energy_wh=None)


def run(config):
    import psutil
    cfg=json.loads(config.read_text(encoding='utf-8'));folder=config.parent
    process=psutil.Process();helpers=[];stop=threading.Event();samples=[]
    flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    trace=folder/'process.jsonl'
    def sample():
        process.cpu_percent(None)
        with trace.open('a',encoding='utf-8') as stream:
            while not stop.is_set():
                try:
                    row=dict(at=time.time(),rss_mb=process.memory_info().rss/1024**2,
                        process_cpu_percent=process.cpu_percent(None),host_cpu_percent=psutil.cpu_percent(None),
                        host_ram_used_mb=psutil.virtual_memory().used/1024**2)
                    samples.append(row);stream.write(json.dumps(row)+'\n');stream.flush()
                except psutil.Error:pass
                stop.wait(.5)
    monitor=threading.Thread(target=sample,daemon=True);monitor.start()
    def gpu_poll():
        command=['nvidia-smi','--query-gpu=timestamp,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.current.sm',
                 '--format=csv,noheader,nounits','--id=0']
        with (folder/'gpu-host.csv').open('w',encoding='utf-8') as stream:
            while not stop.is_set():
                try:
                    reading=subprocess.run(command,capture_output=True,text=True,timeout=3,**flags)
                    if reading.returncode==0:stream.write(reading.stdout);stream.flush()
                except (OSError,subprocess.TimeoutExpired):pass
                stop.wait(1)
    gpu_monitor=threading.Thread(target=gpu_poll,daemon=True);gpu_monitor.start()
    output=dict(status='failed',failure_kind='unknown',config={k:cfg[k] for k in ('id','model','device','compute')})
    start=time.time();timer=time.perf_counter();stages={};result_segments=[]
    cpu_baseline=process.cpu_times()
    atomic_json(folder/'process.json',dict(pid=os.getpid(),created=process.create_time()))
    try:
        for command in ([
            'powershell','-NoProfile','-File',str(ROOT/'scripts/benchmark_gpu_memory.ps1'),
            '-TargetPid',str(os.getpid()),'-OutputPath',str(folder/'gpu-process.jsonl')],):
            try:
                child=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**flags)
                helpers.append(child)
            except OSError:pass
        helper_info=[]
        for helper in helpers:
            try:helper_info.append(dict(pid=helper.pid,created=psutil.Process(helper.pid).create_time()))
            except psutil.NoSuchProcess:pass
        atomic_json(folder/'helpers.json',helper_info)
        atomic_json(folder/'progress.json',dict(stage='baseline',progress=0,at=time.time()))
        time.sleep(10)
        start=time.time();timer=time.perf_counter()
        cpu_baseline=process.cpu_times()
        # Registers packaged CUDA DLL directories without importing the API.
        from server.infer import DLL_HANDLES
        import ctranslate2
        from faster_whisper import WhisperModel
        from faster_whisper.audio import decode_audio
        ctranslate2.set_random_seed(cfg['options']['seed'])
        atomic_json(folder/'progress.json',dict(stage='decoding',progress=0,at=time.time()))
        t=time.perf_counter();audio=decode_audio(cfg['media'],sampling_rate=16000)
        stages['decode_s']=time.perf_counter()-t;duration=len(audio)/16000
        if not 0<duration<=7200:raise ValueError('Unexpected benchmark audio duration')
        atomic_json(folder/'progress.json',dict(stage='loading',progress=0,at=time.time()))
        t=time.perf_counter()
        model=WhisperModel(cfg['model_path'],device=cfg['device'],compute_type=cfg['compute'],
            cpu_threads=cfg['options']['cpu_threads'],num_workers=1,local_files_only=True)
        stages['model_load_s']=time.perf_counter()-t
        effective=model.model.compute_type
        t=time.perf_counter()
        atomic_json(folder/'progress.json',dict(stage='transcribing',progress=0,at=time.time()))
        segments,info=model.transcribe(audio,beam_size=cfg['options']['beam_size'],
            vad_filter=cfg['options']['vad_filter'],language=cfg['options']['language'],
            temperature=cfg['options']['temperature'])
        with (folder/'segments.jsonl').open('w',encoding='utf-8') as stream:
            for segment in segments:
                row=dict(start=segment.start,end=segment.end,text=segment.text,
                    avg_logprob=segment.avg_logprob,no_speech_prob=segment.no_speech_prob,
                    compression_ratio=segment.compression_ratio)
                result_segments.append(row);stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                atomic_json(folder/'progress.json',dict(stage='transcribing',
                    progress=min(99,segment.end/duration*100),audio_end_s=segment.end,at=time.time()))
        stages['asr_s']=time.perf_counter()-t
        elapsed=time.perf_counter()-timer
        text=' '.join(row['text'].strip() for row in result_segments)
        (folder/'transcript.txt').write_text(text,encoding='utf-8')
        output=dict(status='complete',config={k:cfg[k] for k in ('id','model','device','compute')},
            compute_effective=effective,stages=stages,engine_elapsed_s=elapsed,
            duration_s=duration,duration_after_vad_s=info.duration_after_vad,
            asr_rtf=stages['asr_s']/duration,total_rtf=elapsed/duration,
            language=info.language,language_probability=info.language_probability,
            segments=len(result_segments),words=len(words(text)),
            timestamp_overruns=sum(s['end']>duration+.5 for s in result_segments),
            timestamp_overlaps=sum(b['start']<a['end'] for a,b in zip(result_segments,result_segments[1:])),
            model_revision=cfg['model_revision'],media_sha256=cfg['media_sha256'])
        reference=Path(cfg['campaign'])/'youtube-automatic.txt'
        if reference.exists():output['automatic_caption_agreement']=caption_agreement(reference.read_text(encoding='utf-8'),text)
    except BaseException as error:
        message=str(error)
        kind='out_of_memory' if 'out of memory' in message.lower() else 'runtime_error'
        output.update(failure_kind=kind,error_type=type(error).__name__,error_message=message[:2000],
            stages=stages,engine_elapsed_s=time.perf_counter()-timer,partial_segments=len(result_segments))
    finally:
        finished=time.time()
        for helper in helpers:
            try:helper.terminate();helper.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired):
                try:helper.kill()
                except OSError:pass
        stop.set();monitor.join(timeout=3);gpu_monitor.join(timeout=4)
        cpu=process.cpu_times();cpu_seconds=max(0,cpu.user+cpu.system-cpu_baseline.user-cpu_baseline.system)
        active_samples=[s for s in samples if s['at']>=start]
        output.update(instrumentation_version=2,started=start,finished=finished,cpu_seconds=cpu_seconds,
            process_rss_mb_peak=max((s['rss_mb'] for s in samples),default=None),
            process_cpu_percent_peak=max((s['process_cpu_percent'] for s in samples),default=None),
            process_cpu_normalized_mean=cpu_seconds/max(.001,finished-start)*100/(psutil.cpu_count() or 1),
            host_cpu_percent_mean=sum(s['host_cpu_percent'] for s in active_samples)/max(1,len(active_samples)),
            host_ram_used_mb_peak=max((s['host_ram_used_mb'] for s in samples),default=None))
        output.update(gpu_summary(folder/'gpu-host.csv',start))
        try:
            rows=[json.loads(line) for line in (folder/'gpu-process.jsonl').read_text(encoding='utf-8-sig').splitlines()]
            values=[r['dedicated_bytes']/1024**2 for r in rows if r.get('available') and r.get('dedicated_bytes') is not None]
            output['process_gpu_dedicated_mb_peak']=max(values) if values else None
            output['process_gpu_memory_source']='Windows GPU Process Memory counters' if values else 'unavailable'
        except (OSError,ValueError):
            output.update(process_gpu_dedicated_mb_peak=None,process_gpu_memory_source='unavailable')
        atomic_json(folder/'result.json',output)
        atomic_json(folder/'progress.json',dict(stage=output['status'],progress=100,at=time.time()))


if __name__=='__main__':run(Path(sys.argv[1]).resolve())
