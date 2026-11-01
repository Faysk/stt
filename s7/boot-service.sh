#!/data/data/com.termux/files/usr/bin/sh
# Termux:Boot entry. Wait for SD; never redirect persistent data to internal storage.
PREFIX=${PREFIX:-/data/data/com.termux/files/usr}
HOME=${HOME:-/data/data/com.termux/files/home}
export PREFIX HOME
export PATH="$PREFIX/bin:/system/bin"
app="$HOME/storage/external-1/stt-worker"
attempt=0
while [ "$attempt" -lt 60 ]; do
    if [ -s "$app/worker.py" ]; then
        exec sh "$HOME/.local/share/stt-services/start-service.sh"
    fi
    attempt=$((attempt+1))
    sleep 5
done
exit 1
