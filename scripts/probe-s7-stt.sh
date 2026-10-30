#!/bin/sh
# S7/Termux STT preflight: read-only, offline, no installs, no model loading.
set -u
emit() {
  key=$1
  val=$(printf '%s' "$2" | tr '\r\n' '  ' | cut -c 1-128)
  [ -n "$val" ] || val=unavailable
  printf '%s=%s\n' "$key" "$val"
}
prop() {
  if command -v getprop >/dev/null 2>&1; then getprop "$1" 2>/dev/null; fi
}
mem() {
  awk -v k="$1" '$1==k":" {printf "%.0f", $2/1024; exit}' /proc/meminfo 2>/dev/null
}
free_space() {
  if [ -d "$1" ]; then
    df -Pk "$1" 2>/dev/null | awk 'NR==2 {printf "%.0f", $4/1024; exit}'
  fi
}
tool() {
  if command -v "$1" >/dev/null 2>&1; then emit "tool_$2" present; else emit "tool_$2" absent; fi
}
emit probe_version 1
emit probe_policy read_only_no_install_no_network
emit android_release "$(prop ro.build.version.release)"
emit android_sdk "$(prop ro.build.version.sdk)"
emit android_abi "$(prop ro.product.cpu.abi)"
emit device_model "$(prop ro.product.model)"
emit kernel_arch "$(uname -m 2>/dev/null)"
emit kernel_release "$(uname -r 2>/dev/null)"
emit cpu_online_range "$(cat /sys/devices/system/cpu/online 2>/dev/null)"
emit ram_total_mib "$(mem MemTotal)"
emit ram_available_mib "$(mem MemAvailable)"
emit internal_free_mib "$(free_space "$HOME")"
emit sd_free_mib "$(free_space "$HOME/storage/external-1")"
if [ -r /sys/class/power_supply/battery/temp ]; then
  temp=$(cat /sys/class/power_supply/battery/temp 2>/dev/null || :)
  case "$temp" in
    ''|*[!0-9-]*) emit battery_temp_c unavailable ;;
    *) emit battery_temp_c "$(awk -v t="$temp" 'BEGIN {printf "%.1f",t/10}' </dev/null)" ;;
  esac
else
  emit battery_temp_c unavailable
fi
for name in ffmpeg ffprobe cmake clang make whisper-cli whisper-bench git; do
  key=$(printf '%s' "$name" | tr '-' '_')
  tool "$name" "$key"
done
python_cmd=''
if command -v python >/dev/null 2>&1; then python_cmd=python
elif command -v python3 >/dev/null 2>&1; then python_cmd=python3
fi
if [ -n "$python_cmd" ]; then
  emit tool_python present
  "$python_cmd" - <<'PY'
import importlib.util
import platform
import sys
print("python_version=" + platform.python_version())
print("python_platform=" + sys.platform)
for name in ("faster_whisper", "ctranslate2", "numpy", "httpx", "av"):
    try:
        installed = importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        installed = False
    print("python_module_" + name + "=" + ("present" if installed else "absent"))
PY
else
  emit tool_python absent
fi
emit inference_test not_run
emit next_step review_environment_before_attempting_ASR
