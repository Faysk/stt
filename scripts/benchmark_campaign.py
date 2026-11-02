"""Sequential, resumable full-video benchmark. Only owns its own trial processes."""
import argparse
import csv
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.benchmarking import atomic_json,file_hash


def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default


def campaign_path(value):
    path=Path(value).resolve()
    if not path.is_relative_to((ROOT/'benchmarks').resolve()):
        raise ValueError('Campaign must be inside the ignored benchmarks directory')
    return path


def lock(path):
    import msvcrt
    stream=path.open('a+b')
    if path.stat().st_size==0:stream.write(b'1');stream.flush()
    stream.seek(0)
    try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:stream.close();return None
    return stream


def verified_process(info,config):
    import psutil
    try:
        process=psutil.Process(info['pid'])
        args=process.cmdline()
        return process if (abs(process.create_time()-info['created'])<.01
            and str(config) in args and str(ROOT/'scripts/benchmark_trial.py') in args) else None
    except (psutil.Error,KeyError,TypeError):return None


def alive(process):
    import psutil
    try:return process.is_running() and process.status()!=psutil.STATUS_ZOMBIE
    except psutil.Error:return False


def stop_trial(process,config):
    info=dict(pid=process.pid,created=process.create_time())
    if verified_process(info,config) is None:return
    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                   capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)


def cleanup_helpers(folder):
    import psutil
    for info in read(folder/'helpers.json',[]):
        try:
            process=psutil.Process(info['pid']);args=process.cmdline()
            if abs(process.create_time()-info['created'])>=.01:continue
            if not any(str(folder) in arg for arg in args):continue
            if process.name().lower() not in ('nvidia-smi.exe','powershell.exe'):continue
            process.kill()
        except (psutil.Error,KeyError,TypeError):pass


def api_client():
    import httpx
    from dotenv import dotenv_values
    env=dotenv_values(ROOT/'.env')
    return httpx.Client(base_url=env['STT_URL'],headers={
        'x-admin-key':env['STT_ADMIN_KEY'],'x-edge-key':env.get('STT_EDGE_KEY',''),
        'user-agent':'Mozilla/5.0'},timeout=15)


def power_state():
    with api_client() as client:
        response=client.get('/api/admin');response.raise_for_status()
        data=response.json()
    item=next(w for w in data['workers'] if w['id']=='notebook')
    return item,data


def reserve(folder,retake=False):
    existing=read(folder/'reservation.json')
    item,data=power_state()
    if existing and not existing.get('restored'):
        if not item['enabled']:return
        if not retake:raise RuntimeError('Notebook enabled manually; campaign will not override it')
        if any(j['status']=='running' and j.get('worker')=='notebook' for j in data['jobs']):
            raise RuntimeError('Wait for the public job before explicitly resuming benchmark')
        with api_client() as client:
            response=client.put('/api/admin/workers/notebook/power',json={'enabled':False});response.raise_for_status()
        return
    if any(j['status']=='running' and j.get('worker')=='notebook' for j in data['jobs']):
        raise RuntimeError('Notebook has a public job; wait for it before reserving')
    atomic_json(folder/'reservation.json',dict(worker='notebook',was_enabled=item['enabled'],restored=False))
    with api_client() as client:
        response=client.put('/api/admin/workers/notebook/power',json={'enabled':False});response.raise_for_status()


def restore(folder):
    reservation=read(folder/'reservation.json')
    if not reservation or reservation.get('restored'):return
    with api_client() as client:
        response=client.put('/api/admin/workers/notebook/power',json={'enabled':reservation['was_enabled']})
        response.raise_for_status()
    reservation.update(restored=True,restored_at=time.time());atomic_json(folder/'reservation.json',reservation)


def completed(folder,row):
    for path in reversed(sorted((folder/'runs'/row['id']).glob('attempt-*/result.json'))):
        if (path.parent/'excluded.json').exists():continue
        result=read(path)
        if result and result.get('instrumentation_version',0)>=2:
            return dict(row,**result,artifact=str(path.relative_to(folder)).replace('\\','/'))
    return None


def report(folder,cfg,current=None):
    rows=[]
    for row in cfg['plan']:
        result=completed(folder,row)
        rows.append(result or dict(row,status='pending'))
    done=sum(row['status']!='pending' for row in rows)
    good=sum(row['status']=='complete' for row in rows)
    value=dict(updated=time.time(),id=cfg['id'],source=cfg['source'],total=len(rows),finished=done,
        successful=good,failed=done-good,current=current,rows=rows,
        accuracy_note='Legendas automáticas não revisadas: WER abaixo mede divergência, não precisão real.')
    atomic_json(folder/'report-data.json',value)
    columns=('id','status','model','device','compute','compute_effective','duration_s','engine_elapsed_s',
        'asr_rtf','total_rtf','cpu_seconds','process_cpu_normalized_mean','process_rss_mb_peak',
        'process_gpu_dedicated_mb_peak','gpu_host_used_mb_peak','gpu_board_power_w_mean',
        'gpu_board_energy_wh','gpu_temperature_c_peak','language','words','failure_kind')
    output=io.StringIO();writer=csv.DictWriter(output,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    (folder/'comparison.csv').write_text(output.getvalue(),encoding='utf-8-sig')
    summary=(f"# Benchmark integral — {cfg['source']['title']}\n\n"
        f"{done}/{len(rows)} combinações finalizadas; {good} concluídas, {done-good} falhas.\n\n"
        "Cada execução usa o vídeo inteiro, processo novo, pesos fixados, beam 5, VAD e oito threads CPU.\n"
        "Energia registrada é da placa GPU inteira, incluindo outros processos; energia CPU não medida.\n"
        "WER/CER contra legendas automáticas representam concordância, não precisão humana.\n\n"
        "Veja comparison.csv, report.html e os arquivos de cada execução em runs/.\n")
    (folder/'SUMMARY.md').write_text(summary,encoding='utf-8')
    return value


def report_server(folder):
    """Local-only viewer, adopt only an exact matching static-server command."""
    import psutil
    import socket
    import shutil
    shutil.copyfile(ROOT/'scripts/benchmark_report.html',folder/'report.html')
    for process in psutil.process_iter():
        try:
            args=process.cmdline()
            if ('http.server' in args and str(folder) in args and '127.0.0.1' in args):
                port=int(args[args.index('http.server')+1])
                atomic_json(folder/'report-server.json',dict(pid=process.pid,port=port,url=f'http://127.0.0.1:{port}/report.html'))
                return port
        except (psutil.Error,ValueError,IndexError):pass
    for port in range(8877,8898):
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError:continue
        with (folder/'report-server.log').open('ab') as log:
            child=subprocess.Popen([str(ROOT/'.venv/Scripts/pythonw.exe'),'-m','http.server',str(port),
                '--bind','127.0.0.1','--directory',str(folder)],cwd=ROOT,stdin=subprocess.DEVNULL,
                stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
        atomic_json(folder/'report-server.json',dict(pid=child.pid,port=port,url=f'http://127.0.0.1:{port}/report.html'))
        return port
    raise RuntimeError('No free loopback report port; existing listeners preserved')


def install_startup(folder):
    import winreg
    command=subprocess.list2cmdline([str(ROOT/'.venv/Scripts/pythonw.exe'),
        str(Path(__file__).resolve()),'run',str(folder)])
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        try:old,_=winreg.QueryValueEx(key,'FayskSTTBenchmark')
        except FileNotFoundError:old=command
        if old!=command:raise RuntimeError('Different benchmark startup entry exists; not overwritten')
        winreg.SetValueEx(key,'FayskSTTBenchmark',0,winreg.REG_SZ,command)


def uninstall_startup(folder):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:
        try:value,_=winreg.QueryValueEx(key,'FayskSTTBenchmark')
        except FileNotFoundError:return
        if str(folder) in value:winreg.DeleteValue(key,'FayskSTTBenchmark')


def run(folder,explicit_resume=False):
    import psutil
    cfg=read(folder/'campaign.json')
    if not cfg:raise ValueError('Prepare campaign first')
    guard=lock(folder/'campaign.lock')
    if guard is None:return
    if (folder/'PAUSE').exists():
        atomic_json(folder/'state.json',dict(stage='paused',updated=time.time()));guard.close();return
    if read(folder/'state.json',{}).get('stage')=='complete':
        restore(folder);uninstall_startup(folder);return
    if file_hash(cfg['media'])!=cfg['media_sha256']:raise ValueError('Input media hash changed')
    try:
        report_server(folder)
        reserve(folder,retake=explicit_resume)
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        for row in cfg['plan']:
            if completed(folder,row):continue
            if (folder/'PAUSE').exists():
                atomic_json(folder/'state.json',dict(stage='paused',updated=time.time()));return
            base=folder/'runs'/row['id'];base.mkdir(parents=True,exist_ok=True)
            attempts=sorted(base.glob('attempt-*'))
            active=attempts[-1] if attempts else None
            process=None
            if active:
                process=verified_process(read(active/'process.json',{}),active/'job.json')
                if process is None:cleanup_helpers(active)
            if process is None:
                active=base/f'attempt-{len(attempts)+1:03}'
                active.mkdir()
                trial=dict(row,campaign=str(folder),media=cfg['media'],media_sha256=cfg['media_sha256'],
                    options=cfg['options'],model_path=cfg['models'][row['model']]['path'],
                    model_revision=cfg['models'][row['model']]['revision'])
                atomic_json(active/'job.json',trial)
                with (active/'stdout.log').open('ab') as out,(active/'stderr.log').open('ab') as err:
                    child=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),
                        str(ROOT/'scripts/benchmark_trial.py'),str(active/'job.json')],cwd=ROOT,
                        stdin=subprocess.DEVNULL,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
                process=psutil.Process(child.pid)
                # Windows venv launcher may spawn the base Python process. The
                # child also writes its precise PID once initialization starts.
                launched=time.time()
                while time.time()-launched<15:
                    exact=verified_process(read(active/'process.json',{}),active/'job.json')
                    if exact is not None:process=exact;break
                    if not process.is_running():break
                    time.sleep(.25)
            print('Running '+row['id'],flush=True)
            last_check=0
            interrupted=False
            while alive(process):
                # A completed result is published only after inference, helpers
                # and telemetry have finished; do not wait on stale launcher PID.
                if (active/'result.json').exists():break
                if (folder/'PAUSE').exists():
                    stop_trial(process,active/'job.json');interrupted=True;break
                if time.time()-process.create_time()>cfg['trial_timeout_seconds']:
                    stop_trial(process,active/'job.json')
                    atomic_json(active/'result.json',dict(status='failed',instrumentation_version=2,
                        failure_kind='timeout_24h',config=row));break
                if time.time()-last_check>=30:
                    last_check=time.time()
                    try:
                        worker,_=power_state()
                        if worker['enabled']:
                            (folder/'PAUSE').write_text('Notebook enabled during campaign',encoding='utf-8')
                            stop_trial(process,active/'job.json');interrupted=True;break
                    except Exception:pass  # API outage cannot start public work.
                progress=read(active/'progress.json',{})
                current=dict(row,progress=progress,attempt=active.name,pid=process.pid)
                atomic_json(folder/'state.json',dict(stage='running',current=current,updated=time.time()))
                report(folder,cfg,current)
                time.sleep(5)
            cleanup_helpers(active)
            if interrupted:
                atomic_json(active/'interrupted.json',dict(at=time.time(),reason='operator_pause'))
                atomic_json(folder/'state.json',dict(stage='paused',updated=time.time()));return
            result=read(active/'result.json')
            if result and result.get('instrumentation_version',0)<2:
                # Initial instrument validation is retained, but not mixed into
                # the comparison. A fresh full-video run follows in version 2.
                atomic_json(active/'pilot.json',dict(excluded=True,reason='telemetry_calibration'))
                return run_after_unlock(folder,guard)
            if not result:
                atomic_json(active/'result.json',dict(status='failed',instrumentation_version=2,
                    failure_kind='process_exit_without_result',config=row))
            report(folder,cfg)
            time.sleep(15)
        atomic_json(folder/'state.json',dict(stage='complete',updated=time.time()))
        report(folder,cfg);restore(folder);uninstall_startup(folder)
        print('CAMPAIGN COMPLETE: all combinations attempted; notebook restored.',flush=True)
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        guard.close()


def run_after_unlock(folder,guard):
    guard.close()
    return run(folder)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('run','status','pause','resume','restore','install-startup','report','serve'))
    p.add_argument('folder',type=Path)
    args=p.parse_args();folder=campaign_path(args.folder)
    if args.action=='run':
        while True:
            try:run(folder);break
            except Exception as error:
                atomic_json(folder/'coordinator-error.json',dict(error_type=type(error).__name__,at=time.time()))
                print('Coordinator retry: '+type(error).__name__,flush=True)
                time.sleep(30)
    elif args.action=='status':print(json.dumps(read(folder/'state.json',{}),ensure_ascii=False,indent=2))
    elif args.action=='pause':(folder/'PAUSE').write_text('Operator requested pause',encoding='utf-8')
    elif args.action=='resume':(folder/'PAUSE').unlink(missing_ok=True);run(folder,explicit_resume=True)
    elif args.action=='restore':restore(folder)
    elif args.action=='install-startup':install_startup(folder)
    elif args.action=='report':report(folder,read(folder/'campaign.json'))
    elif args.action=='serve':print('Report: http://127.0.0.1:'+str(report_server(folder))+'/report.html')
