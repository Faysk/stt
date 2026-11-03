"""Offline tests for the read-only S7 incident preflight. No SSH or real credentials."""
import importlib.util
from pathlib import Path
import pytest

path=Path(__file__).resolve().parents[1]/"scripts/preflight_s7_1027.py"
spec=importlib.util.spec_from_file_location("s7_1027_preflight",path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def good_report():
    return {
        "api_loopback_tcp":True,"api_env_private":True,"scoped_key_private":True,
        "edge_secret_present":True,"require_edge_enabled":True,
        "database_present":True,"on_external_volume":True,
        "queue_checked":True,"jobs_queued":0,"jobs_active":0,"outbox_count":0,
        "worker_entrypoint_local":True,"worker_runit_running":True,
        "runtime_worker_sha256":"worker-sha",
        "runtime_entrypoint_sha256":"entry-sha",
    }


def test_after_phase_requires_local_auth_artifacts_and_empty_queue():
    result=module.assess(good_report(),"worker-sha","entry-sha","after")
    assert result["checks_passed"] and not result["blockers"]
    state=good_report()
    state["jobs_active"]=1
    state["outbox_count"]=1
    result=module.assess(state,"worker-sha","entry-sha","after")
    assert not result["checks_passed"]
    assert "queue_not_drained" in result["blockers"]
    assert "outbox_not_drained_or_unreadable" in result["blockers"]


def test_before_detects_version_mismatch_but_does_not_claim_deploy():
    state=good_report()
    state["runtime_worker_sha256"]="older"
    state["runtime_entrypoint_sha256"]="older"
    state["worker_entrypoint_local"]=False
    result=module.assess(state,"worker-sha","entry-sha","before")
    assert result["checks_passed"]
    assert len(result["version_mismatches"])==2
    result=module.assess(state,"worker-sha","entry-sha","after")
    assert not result["checks_passed"]
    assert "worker_entrypoint_local" in result["blockers"]


def test_unknown_data_fail_closed_and_never_interpreted_as_zero():
    state=good_report()
    state["jobs_queued"]=None
    state["outbox_count"]=None
    state["queue_checked"]=False
    result=module.assess(state,"worker-sha","entry-sha","after")
    assert not result["checks_passed"]
    assert "queue_not_verified" in result["blockers"]
    assert "outbox_not_drained_or_unreadable" in result["blockers"]
    assert "queue_checked" in result["blockers"]


def test_ssh_option_injection_is_rejected():
    for host,user in [("-oProxyCommand=sh","u0_a279"),("host/with/slash","u0_a279"),
                      ("192.168.1.2","-oProxyCommand"),("host","user@evil")]:
        with pytest.raises(ValueError):
            module.valid_target(host,user)
    module.valid_target("192.168.1.2","u0_a279")


def test_readonly_probe_discloses_no_keys_and_uses_read_only_sqlite():
    code=module.REMOTE
    assert "?mode=ro" in code
    assert "SELECT status,COUNT(*) FROM jobs GROUP BY status" in code
    assert "socket.create_connection(('127.0.0.1',8786)" in code
    assert "STT_EDGE_KEY" in code
    assert "json.dumps(result,sort_keys=True)" in code
    assert "returncode == 0" in code
    assert "subprocess.run(['/data/data/com.termux/files/usr/bin/sv','status'" in code
    for forbidden in ("os.chmod(","shutil.rmtree(","INSERT INTO","UPDATE jobs","sv restart","cloudflared tunnel"):
        assert forbidden not in code
