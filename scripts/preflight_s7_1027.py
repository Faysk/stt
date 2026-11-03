"""Read-only preflight for the S7-authoritative STT architecture (#97-#99).

No uploads, restarts, file writes, tokens, transcript content, or Cloudflare calls.
Uses existing verified SSH identity/known_hosts; produces aggregated counters only.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REMOTE = r'''
import hashlib, json, os, socket, sqlite3, stat, subprocess
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

home = Path.home()
api = home / '.local/share/stt-api/runtime/.env'
scope = home / '.stt-s7-worker-key'
service = home / '.local/share/stt-services/worker/run'
api_run = home / '.local/share/stt-services/api/run'
worker = home / 'stt-lab/stt-worker.py'
data = home / 'storage/external-1/stt-api/data'
db_file = data / 'db/stt.sqlite3'

def private(path):
    try:
        return bool(path.is_file() and not path.is_symlink()
                    and stat.S_IMODE(path.stat().st_mode) & 0o077 == 0)
    except OSError:
        return False

def digest(path):
    try:
        if not path.is_file() or path.is_symlink():
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None

def config_checks():
    flags = {'edge_secret_present': False, 'require_edge_enabled': False}
    if not private(api):
        return flags
    try:
        for line in api.read_text(encoding='utf-8').splitlines():
            name, sep, value = line.partition('=')
            if not sep or name not in ('STT_EDGE_KEY', 'STT_REQUIRE_EDGE'):
                continue
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = None
            if name == 'STT_EDGE_KEY':
                flags['edge_secret_present'] = isinstance(parsed,str) and len(parsed) >= 32
            else:
                flags['require_edge_enabled'] = parsed == '1'
    except (OSError, UnicodeError):
        pass
    return flags

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def origin_guard_http():
    """Confirm security locally, without leaking the edge secret or using Cloudflare."""
    result = {'origin_no_key_403':False,'origin_private_key_200':False}
    if not private(api):
        return result
    edge_key = None
    try:
        for line in api.read_text(encoding='utf-8').splitlines():
            if line.startswith('STT_EDGE_KEY='):
                edge_key = json.loads(line.partition('=')[2])
                break
    except (OSError,UnicodeError,ValueError):
        return result
    if not isinstance(edge_key,str) or len(edge_key)<32 or any(
        c in edge_key for c in '\\r\\n\\x00'
    ):
        return result
    opener = build_opener(ProxyHandler({}),NoRedirect())
    url = 'http://127.0.0.1:8786/api/health'
    def status(headers):
        request=Request(url,headers=headers,method='GET')
        try:
            with opener.open(request,timeout=3) as response:
                return response.status
        except HTTPError as exc:
            return exc.code
        except (OSError,URLError,ValueError):
            return None
    result['origin_no_key_403'] = status({}) == 403
    # The credential is used strictly on S7 loopback and never printed.
    result['origin_private_key_200'] = status({'x-edge-key':edge_key}) == 200
    return result

def queue_counters():
    outcome = {'database_present':False,'on_external_volume':False,
               'queue_checked':False,'jobs_queued':None,'jobs_active':None}
    try:
        outcome['database_present'] = bool(db_file.is_file() and not db_file.is_symlink())
        external = home/'storage/external-1'
        outcome['on_external_volume'] = external.is_dir() and external.stat().st_dev != home.stat().st_dev
        if not outcome['database_present'] or not outcome['on_external_volume']:
            return outcome
        conn = sqlite3.connect('file:' + quote(str(db_file)) + '?mode=ro', uri=True, timeout=2)
        try:
            rows = dict(conn.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status').fetchall())
            outcome['jobs_queued'] = int(rows.get('queued',0))
            outcome['jobs_active'] = sum(int(rows.get(s,0)) for s in ('running','uploading'))
            outcome['queue_checked'] = True
        finally:
            conn.close()
    except (OSError, sqlite3.Error, ValueError):
        pass
    return outcome

def worker_running():
    try:
        result = subprocess.run(['/data/data/com.termux/files/usr/bin/sv','status',str(home/'.local/share/stt-services/worker')],
                                capture_output=True,text=True,timeout=3,check=False)
        return result.returncode == 0 and result.stdout.startswith('run:')
    except (OSError, subprocess.TimeoutExpired):
        return False

def listens_loopback():
    try:
        with socket.create_connection(('127.0.0.1',8786),timeout=2):
            return True
    except OSError:
        return False

def entrypoint_is_local():
    try:
        lines = service.read_text(encoding='utf-8')
        api_text = api_run.read_text(encoding='utf-8')
        return ('STT_URL=http://127.0.0.1:8786' in lines and
                'STT_S7_LOCAL_API_ENV=' in lines and
                '--host 127.0.0.1 --port 8786' in api_text)
    except (OSError,UnicodeError):
        return False

def outbox_count():
    """Fail closed on missing/unreadable workspaces and partial result spools.

    atomic_json writes result-<id>.json.<random>.tmp before rename; ignoring
    those artifacts could incorrectly certify a clean outbox after a crash.
    """
    try:
        primary = home/'.cache/stt-worker'
        legacy = home/'stt-lab/stt-worker'
        folders = []
        for folder in (primary, legacy):
            if folder.is_symlink():
                return None
            if folder.exists():
                if not folder.is_dir():
                    return None
                folders.append(folder)
        if not folders:
            return None
        total = 0
        for folder in folders:
            for item in folder.glob('result-*'):
                if item.is_symlink() or not item.is_file():
                    return None
                # Include atomic_json's .json.<random>.tmp and unknown
                # result-* artifacts instead of assuming they are harmless.
                total += 1
        return total
    except (OSError, PermissionError):
        return None

result = {
    'api_loopback_tcp':listens_loopback(),
    'api_env_private':private(api),
    'scoped_key_private':private(scope),
    'worker_entrypoint_local':entrypoint_is_local(),
    'worker_runit_running':worker_running(),
    'runtime_worker_sha256':digest(worker),
    'runtime_entrypoint_sha256':digest(service),
    'outbox_count':outbox_count(),
    'primary_workspace_present':(
        (home/'.cache/stt-worker').is_dir()
        and not (home/'.cache/stt-worker').is_symlink()),
}
result.update(config_checks())
result.update(origin_guard_http())
result.update(queue_counters())
print(json.dumps(result,sort_keys=True))
'''

def valid_target(host, user):
    """Reject SSH option injection and ambiguous host/user syntax."""
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', host) or host.startswith('-'):
        raise ValueError('Invalid SSH host')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,64}',user):
        raise ValueError('Invalid SSH user')

def assess(report, expected_worker, expected_entry, phase):
    must = ('api_loopback_tcp','api_env_private','scoped_key_private',
            'edge_secret_present','require_edge_enabled',
            'origin_no_key_403','origin_private_key_200','database_present',
            'on_external_volume','queue_checked')
    blockers = [k for k in must if report.get(k) is not True]
    if report.get('jobs_active') is None or report.get('jobs_queued') is None:
        blockers.append('queue_not_verified')
    elif report['jobs_active'] != 0 or report['jobs_queued'] != 0:
        blockers.append('queue_not_drained')
    if report.get('outbox_count') is None or report['outbox_count'] > 0:
        blockers.append('outbox_not_drained_or_unreadable')
    mismatched = [k for k,expected in (
        ('runtime_worker_sha256',expected_worker),
        ('runtime_entrypoint_sha256',expected_entry)
    ) if report.get(k) != expected]
    if phase == 'after':
        # An active installed S7 worker must use its private current workspace.
        if report.get('primary_workspace_present') is not True:
            blockers.append('primary_workspace_missing')
        for k in ('worker_entrypoint_local','worker_runit_running'):
            if report.get(k) is not True: blockers.append(k)
        blockers.extend(mismatched)
    return {'phase':phase,'blockers':sorted(set(blockers)),
            'version_mismatches':mismatched,
            'checks_passed':not blockers,
            'note':('Snapshot only: no writes or service restarts; verify no new jobs '
                    'immediately before any worker-only reload.')}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',required=True)
    parser.add_argument('--user',required=True)
    parser.add_argument('--identity',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8022)
    parser.add_argument('--phase',choices=['before','after'],default='before')
    parser.add_argument('--host-key-alias')
    args=parser.parse_args(argv)
    try:
        valid_target(args.host,args.user)
        if args.host_key_alias:
            valid_target(args.host_key_alias,'unused')
        if not 1<=args.port<=65535:
            raise ValueError('Invalid SSH port')
        if not args.identity.is_file():
            raise ValueError('SSH identity file not found')
        ssh=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
             '-o','IdentitiesOnly=yes','-o','ConnectTimeout=8',
             '-i',str(args.identity),'-p',str(args.port)]
        if args.host_key_alias:
            ssh+=['-o','HostKeyAlias='+args.host_key_alias]
        ssh += [args.user+'@'+args.host,'python -']
        run=subprocess.run(ssh,input=REMOTE,text=True,capture_output=True,timeout=24,check=False)
        if run.returncode != 0:
            raise ValueError('S7 read-only SSH probe failed (details deliberately omitted)')
        result=json.loads(run.stdout.strip())
        if not isinstance(result,dict):
            raise ValueError('Unexpected read-only probe output')
        expected_worker=hashlib.sha256((ROOT/'s7/worker.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        expected_entry=hashlib.sha256((ROOT/'s7/service-run.sh').read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        summary=assess(result,expected_worker,expected_entry,args.phase)
        # No secrets or media identifiers in either result or summary.
        print(json.dumps({'report':result,'assessment':summary},sort_keys=True,indent=2))
        return 0 if summary['checks_passed'] else 2
    except (ValueError,OSError,subprocess.TimeoutExpired,json.JSONDecodeError):
        print('Preflight unavailable: check SSH, host key, API and storage locally. No service changed.',
              file=sys.stderr)
        return 2

if __name__=='__main__':
    raise SystemExit(main())
