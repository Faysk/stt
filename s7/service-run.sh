#!/data/data/com.termux/files/usr/bin/sh
set -eu
PREFIX=${PREFIX:-/data/data/com.termux/files/usr}
HOME=${HOME:-/data/data/com.termux/files/home}
export PREFIX HOME
export PATH="$PREFIX/bin:/system/bin"
export STT_URL=https://stt.faysk.dev
export STT_S7_WORK="$HOME/.cache/stt-worker"
export STT_S7_ROOT="$HOME/stt-lab/whisper.cpp"
app="$HOME/storage/external-1/stt-worker"
test -s "$app/worker.py" || exit 1
# Private runtime copy: Android shared storage must not supply executable code
# to a process that can read the scoped key.
exec "$PREFIX/bin/python" -u "$HOME/stt-lab/stt-worker.py"
