"""Persistent bounded-resolution metrics. No IPs, tokens, filenames or transcripts."""
import json
import os
import platform
import subprocess
import time
from importlib.metadata import version, PackageNotFoundError

import psutil

PERIODS={'minute':(900,15),'hour':(3600,60),'day':(86400,900),'week':(7*86400,3600),
         'month':(30*86400,86400),'year':(365*86400,7*86400),'years':(5*365*86400,30*86400)}

def package_versions():
    result={'python':platform.python_version()}
    for name in ('faster-whisper','ctranslate2','av','yt-dlp'):
        try: result[name]=version(name)
        except PackageNotFoundError: result[name]=None
    return result

def gpu_info():
    try:
        kwargs={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        r=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,**kwargs)
        if r.returncode or not r.stdout.strip(): return None
        parts=[p.strip() for p in r.stdout.splitlines()[0].split(',')]
        def num(x):
            try: return float(x)
            except ValueError: return None
        return dict(name=parts[0],driver=parts[1],utilization=num(parts[2]),used_mb=num(parts[3]),total_mb=num(parts[4]),temperature=num(parts[5]),power_w=num(parts[6]))
    except (OSError,subprocess.TimeoutExpired,IndexError): return None

def install(c):
    c.executescript('''
      CREATE TABLE IF NOT EXISTS telemetry (
        step INTEGER,bucket INTEGER,worker TEXT,n INTEGER,cpu_sum REAL,cpu_max REAL,
        ram_sum REAL,ram_max REAL,gpu_sum REAL,gpu_n INTEGER,gpu_max REAL,
        queue_sum REAL,queue_max INTEGER,free_gb REAL,
        PRIMARY KEY(step,bucket,worker));
      CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,at REAL,job TEXT,kind TEXT,actor TEXT,data TEXT);
      CREATE TABLE IF NOT EXISTS observability_meta (key TEXT PRIMARY KEY,value REAL);
      CREATE INDEX IF NOT EXISTS events_job ON events(job,at);
      CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created);
    ''')

def event(c,jid,kind,actor='system',data=None):
    c.execute('INSERT INTO events(at,job,kind,actor,data) VALUES(?,?,?,?,?)',(time.time(),jid,kind,actor,json.dumps(data or {})))

def record(c,worker,cpu,ram,gpu,free_gb,queue,now=None):
    now=time.time() if now is None else now
    c.execute("INSERT OR IGNORE INTO observability_meta VALUES('collection_started',?)",(now,))
    for step in (15,60,3600,86400):
        bucket=int(now//step)*step
        c.execute('''INSERT INTO telemetry VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(step,bucket,worker) DO UPDATE SET
          n=n+1,cpu_sum=cpu_sum+excluded.cpu_sum,cpu_max=max(cpu_max,excluded.cpu_max),
          ram_sum=ram_sum+excluded.ram_sum,ram_max=max(ram_max,excluded.ram_max),
          gpu_sum=gpu_sum+excluded.gpu_sum,gpu_n=gpu_n+excluded.gpu_n,
          gpu_max=CASE WHEN excluded.gpu_max IS NULL THEN gpu_max WHEN gpu_max IS NULL THEN excluded.gpu_max ELSE max(gpu_max,excluded.gpu_max) END,
          queue_sum=queue_sum+excluded.queue_sum,queue_max=max(queue_max,excluded.queue_max),free_gb=excluded.free_gb''',
          (step,bucket,worker,1,cpu,cpu,ram,ram,gpu or 0,int(gpu is not None),gpu,queue,queue,free_gb))
    c.execute('DELETE FROM telemetry WHERE (step=15 AND bucket<?) OR (step=60 AND bucket<?) OR (step=3600 AND bucket<?)',(now-86400,now-7*86400,now-90*86400))

def history(c,period,now=None):
    now=time.time() if now is None else now
    seconds,step=PERIODS[period]
    start=now-seconds
    source=15 if step==15 else 60 if step<3600 else 3600 if period=='week' else 86400
    points={}
    for r in c.execute('''SELECT CAST(bucket/? AS INTEGER)*? bucket,sum(n) n,sum(cpu_sum) cpu,
        max(cpu_max) cpu_max,sum(ram_sum) ram,max(ram_max) ram_max,sum(gpu_sum) gpu,sum(gpu_n) gpu_n,
        max(gpu_max) gpu_max,sum(queue_sum) queue,max(queue_max) queue_max
        FROM telemetry WHERE step=? AND bucket>=? AND bucket<=? GROUP BY CAST(bucket/? AS INTEGER) ORDER BY bucket''',(step,step,source,start,now,step)):
        row=dict(r)
        points[row['bucket']]={**row,'cpu':row['cpu']/row['n'],'ram':row['ram']/row['n'],
                              'gpu':row['gpu']/row['gpu_n'] if row['gpu_n'] else None,'queue':row['queue']/row['n']}
    jobs={}
    for r in c.execute('''SELECT CAST(created/? AS INTEGER)*? bucket,count(*) submitted,
       sum(CASE WHEN outcome='done' OR status='done' THEN 1 ELSE 0 END) done,
       sum(CASE WHEN outcome='failed' OR status='failed' THEN 1 ELSE 0 END) failed,
       coalesce(sum(duration),0) audio,coalesce(sum(elapsed),0) processing FROM jobs
       WHERE created>=? AND created<=? GROUP BY CAST(created/? AS INTEGER)''',(step,step,start,now,step)):
        jobs[r['bucket']]=dict(r)
    buckets=sorted(set(points)|set(jobs))
    # Empty buckets expose collection gaps as null resources. Event counts remain zero.
    first=int(start//step)*step
    output=[]
    for bucket in range(first,int(now//step)*step+1,step):
        output.append({'bucket':bucket,'cpu':None,'cpu_max':None,'ram':None,'ram_max':None,'gpu':None,'gpu_max':None,'queue':None,'queue_max':None,
                       'n':0,'submitted':0,'done':0,'failed':0,'audio':0,'processing':0,**points.get(bucket,{}),**jobs.get(bucket,{})})
    first_sample=c.execute("SELECT value FROM observability_meta WHERE key='collection_started'").fetchone()
    earliest=first_sample[0] if first_sample else None
    return dict(period=period,step=step,start=start,end=now,collection_started=earliest,points=output,
                attribution='Contagens e tempos de trabalhos agrupados pela hora do envio; recursos são amostras do host da API.')
