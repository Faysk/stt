#!/data/data/com.termux/files/usr/bin/sh
# Isolated runit tree: never starts/stops the Observatory or other Termux services.
set -eu
PREFIX=${PREFIX:-/data/data/com.termux/files/usr}
HOME=${HOME:-/data/data/com.termux/files/home}
export PREFIX HOME
export PATH="$PREFIX/bin:/system/bin"
app="$HOME/storage/external-1/stt-worker"
services="$HOME/.local/share/stt-services"
test -s "$app/worker.py" || exit 1
test -x "$services/worker/run" || exit 1
termux-wake-lock
if sv status "$services/worker" >/dev/null 2>&1; then
    sv up "$services/worker"
    exit 0
fi
umask 077
nohup runsvdir "$services" </dev/null >/dev/null 2>&1 &
