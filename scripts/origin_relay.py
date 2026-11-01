"""Temporary localhost SSH relay for the pre-migration Windows connector.

The S7 connector itself does not use this relay. No startup entry is installed:
after reboot Windows runs only the remote worker and S7 handles ingress.
"""
import os
from pathlib import Path
import socket
import subprocess
import time
from dotenv import dotenv_values
from supervise import ROOT, lock_instance


def main():
    if os.name!='nt':raise SystemExit('Windows only')
    guard=lock_instance(ROOT/'logs/origin-relay.lock')
    if guard is None:return
    with socket.socket() as connection:
        connection.settimeout(1)
        if connection.connect_ex(('127.0.0.1',8786))==0:
            raise SystemExit('RELAY_PORT_ALREADY_OCCUPIED')
    command=['ssh','-NT','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
             '-o','IdentitiesOnly=yes','-o','ExitOnForwardFailure=yes',
             '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
             '-i',str(Path.home()/'.ssh/s7_lmonitor_ed25519'),'-p','8022',
             '-L','127.0.0.1:8786:127.0.0.1:8786','u0_a279@192.168.1.2']
    with (ROOT/'logs/origin-relay.log').open('ab') as log:
        while True:
            subprocess.run(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                           creationflags=subprocess.CREATE_NO_WINDOW,check=False)
            time.sleep(15)


if __name__=='__main__':main()
