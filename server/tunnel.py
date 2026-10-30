"""Runs the dedicated STT Tunnel with named Wrangler auth or a tunnel token.

Named mode uses the operator's existing Wrangler OAuth session. It never
reads token files or displays secrets, and is appropriate for interactive
Windows setups. For unattended Windows services, a dedicated connector token
may still be preferable.
"""
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from server.config import getenv, required


def main():
    load_dotenv()
    name = getenv('STT_TUNNEL_NAME', '')
    token = getenv('STT_TUNNEL_TOKEN', '')
    if bool(name) == bool(token):
        raise RuntimeError('Configure exactly one of STT_TUNNEL_NAME or STT_TUNNEL_TOKEN')
    if name:
        if name != 'faysk-stt':
            raise RuntimeError('STT_TUNNEL_NAME must be faysk-stt')
        root = Path(__file__).resolve().parents[1]
        cli = root / 'node_modules' / 'wrangler' / 'bin' / 'wrangler.js'
        if not cli.is_file():
            raise RuntimeError('Wrangler not installed: run npm ci')
        env = os.environ.copy()
        cloudflared = getenv('STT_CLOUDFLARED', '')
        if cloudflared and Path(cloudflared).is_file():
            env['CLOUDFLARED_PATH'] = str(Path(cloudflared).resolve())
        subprocess.run(['node', str(cli), 'tunnel', 'run', name],
                       cwd=root, env=env, check=True)
    else:
        env = os.environ.copy()
        env['TUNNEL_TOKEN'] = required('STT_TUNNEL_TOKEN')
        subprocess.run([required('STT_CLOUDFLARED'), 'tunnel',
                        '--no-autoupdate', 'run'], env=env, check=True)


if __name__ == '__main__':
    main()
