"""Run locally: python -m scripts.admin_pair [--recover | --revoke-all]."""
import argparse
import sqlite3
import time

from server import app as service
from server import admin_auth


def main():
    parser = argparse.ArgumentParser(description='Pareamento local da passkey STT (sem cadastro público).')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--recover', action='store_true', help='Revoga credenciais/sessões e abre novo pareamento.')
    mode.add_argument('--revoke-all', action='store_true', help='Revoga credenciais, sessões e pedidos, sem novo pareamento.')
    args = parser.parse_args()
    # Snapshot before migration/recovery. Operator must protect the private backup ACL.
    if service.DB.exists():
        backup = service.DB.parent / 'auth-backups' / (time.strftime('%Y%m%d-%H%M%S')+'-'+str(time.time_ns())+'.sqlite3')
        backup.parent.mkdir(exist_ok=True)
        with sqlite3.connect(service.DB) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        print('Snapshot SQLite criado na pasta privada auth-backups. Proteja-o como a base original.')
    service.init()
    if args.revoke_all:
        with service.db() as c:
            c.execute('BEGIN IMMEDIATE')
            admin_auth.revoke_all(c)
            admin_auth.event(c, 'local_revocation')
        print('Passkeys, sessões e pedidos revogados. A chave legada depende da sua feature flag.')
        return
    try:
        url = admin_auth.new_pairing(service.db, recover=args.recover)
    except ValueError as error:
        parser.exit(2, str(error)+'\n')
    print('Abra no celular em até 5 minutos. Link privado de pareamento, não compartilhe:')
    print(url)
    qr = admin_auth.qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.print_ascii(invert=True)


if __name__ == '__main__':
    main()
