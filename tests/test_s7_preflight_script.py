"""Static and syntax checks for S7/Termux no-write probe."""
import os
from pathlib import Path
import subprocess
import pytest

FILE = Path(__file__).resolve().parents[1] / "scripts" / "probe-s7-stt.sh"

def test_s7_probe_policy_and_no_private_host():
    content = FILE.read_text(encoding="utf-8")
    assert "read_only_no_install_no_network" in content
    assert "inference_test not_run" in content
    assert "192.168." not in content and "u0_a" not in content
    for cmd in ("pkg install", "apt install", "curl ", "wget ", "git clone",
                "rm -rf", "chmod ", "termux-wake-lock"):
        assert cmd not in content

@pytest.mark.skipif(os.name == "nt", reason="POSIX shell not installed on Windows")
def test_s7_probe_shell_syntax():
    result = subprocess.run(["sh", "-n", str(FILE)], text=True,
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
