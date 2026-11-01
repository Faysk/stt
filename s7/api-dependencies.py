"""Build the isolated Android API runtime from official Termux/PyPI sources.

Run in Termux with --apply after installing official rust, maturin,
python-cryptography, python-psutil and cloudflared packages (no upgrade).
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
import venv


def main(apply=False):
    home = Path.home()
    sd = home / 'storage/external-1/stt-api-build'
    runtime = home / '.local/share/stt-api-venv'
    temp = home / '.cache/stt-api-build'
    external = home / 'storage/external-1'
    if not external.is_dir() or external.stat().st_dev == home.stat().st_dev:
        raise SystemExit('SD_UNAVAILABLE')
    if shutil.disk_usage(home).free < 3*1024**3:
        raise SystemExit('BUILD_RESERVE_UNAVAILABLE')
    print(json.dumps({'preview':not apply, 'internal_free':shutil.disk_usage(home).free}), flush=True)
    if not apply:
        return
    os.umask(0o077)
    for path in (sd, runtime, temp):
        if path.is_symlink():
            raise SystemExit('SYMLINK_TARGET_REFUSED')
    sd.mkdir(exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)
    if not runtime.exists():
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(runtime)
    # Termux packages the native binary. Bootstrap only the matching, pure
    # Python PEP517 interface from the hash-verified official PyPI sdist.
    version = '1.16.0'
    with urllib.request.urlopen('https://pypi.org/pypi/maturin/'+version+'/json', timeout=30) as response:
        meta = json.load(response)
    source = next(item for item in meta['urls'] if item['packagetype']=='sdist')
    with urllib.request.urlopen(source['url'], timeout=60) as response:
        data = response.read(16*1024**2+1)
    if len(data)>16*1024**2 or hashlib.sha256(data).hexdigest()!=source['digests']['sha256']:
        raise SystemExit('MATURIN_SOURCE_HASH_MISMATCH')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        backend = archive.extractfile('maturin-'+version+'/maturin/__init__.py').read()
    package = runtime / ('lib/python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor)+'/site-packages/maturin')
    package.mkdir(exist_ok=True)
    (package/'__init__.py').write_bytes(backend)
    (sd/'maturin-source.json').write_text(json.dumps({'version':version,'sha256':source['digests']['sha256'],'binary':'Termux official package'}))
    env = dict(os.environ, TMPDIR=str(temp), PIP_CACHE_DIR=str(sd/'pip-cache'),
               CARGO_HOME=str(sd/'cargo'), CARGO_TARGET_DIR=str(temp/'target'),
               CARGO_BUILD_JOBS='2', MATURIN_NO_INSTALL_RUST='1', ANDROID_API_LEVEL='24',
               CBOR2_BUILD_C_EXTENSION='0')
    command = [str(runtime/'bin/python'), '-m', 'pip', 'install', '--no-build-isolation',
               'pydantic-core==2.50.2', 'fastapi==0.162.0', 'uvicorn==0.54.0',
               'python-multipart==0.0.32', 'python-dotenv==1.2.4', 'webauthn==2.8.0',
               'qrcode==8.2', 'cbor2==5.9.0']
    log = sd/'python-dependencies.log'
    with log.open('w') as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True)
        deadline=time.monotonic()+1800
        while process.poll() is None:
            used=0
            for path in temp.rglob('*'):
                try:
                    if path.is_file():used+=path.stat().st_size
                except FileNotFoundError:
                    pass
            if (shutil.disk_usage(home).free < 2*1024**3+128*1024**2
                    or used > 1536*1024**2 or time.monotonic()>deadline):
                os.killpg(process.pid, signal.SIGTERM)
                raise SystemExit('RESERVE_GUARD_STOPPED_BUILD')
            time.sleep(2)
    print(json.dumps({'python_install_exit':process.returncode, 'internal_free':shutil.disk_usage(home).free}))
    if process.returncode:
        print(log.read_text()[-3500:])
        raise SystemExit(process.returncode)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    main(parser.parse_args().apply)
