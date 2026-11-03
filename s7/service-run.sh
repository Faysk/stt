#!/data/data/com.termux/files/usr/bin/sh
set -eu
exec 2>&1
PREFIX=${PREFIX:-/data/data/com.termux/files/usr}
HOME=${HOME:-/data/data/com.termux/files/home}
export PREFIX HOME
export PATH="$PREFIX/bin:/system/bin"
export STT_URL=http://127.0.0.1:8786
# Origin authentication stays enforced; local key is read from private API runtime.
export STT_S7_LOCAL_API_ENV="$HOME/.local/share/stt-api/runtime/.env"
export STT_S7_WORK="$HOME/.cache/stt-worker"
export STT_S7_ROOT="$HOME/stt-lab/whisper.cpp"
app="$HOME/storage/external-1/stt-worker"
test -s "$app/worker.py" || exit 1
# Private runtime copy: Android shared storage must not supply executable code
# to a process that can read the scoped key.
exec "$PREFIX/bin/python" -u "$HOME/stt-lab/stt-worker.py"
