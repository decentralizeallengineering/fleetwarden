import json, os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
import cadence_guard as cg


def run(payload, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("COST_STACK_OFF", "CLAUDE_ROLE")}
    e.update(env)
    r = subprocess.run([sys.executable, H + "/cadence_guard.py"], input=json.dumps(payload), capture_output=True, text=True, env=e)
    return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None


def test_cron_ok_pure():
    assert cg.cron_ok("*/20", 1200) and not cg.cron_ok("*/10", 1200)
    assert cg.cron_ok("7,27,47", 1200) and not cg.cron_ok("7,9", 1200)
    assert not cg.cron_ok("*/20", 0)


def test_every_minute_cron_denied_hourly_allowed():
    assert run({"tool_name": "CronCreate", "tool_input": {"cron": "* * * * *"}})["permissionDecision"] == "deny"
    assert run({"tool_name": "CronCreate", "tool_input": {"cron": "5 * * * *"}}) is None


def test_wakeup_raised_to_floor(tmp_path):
    exe = tmp_path / "tp"; exe.write_text("#!/bin/sh\necho 3600 quiet\n"); exe.chmod(0o755)
    out = run({"tool_name": "ScheduleWakeup", "tool_input": {"delaySeconds": 60}}, TICK_POLICY=str(exe))
    assert out["updatedInput"]["delaySeconds"] == 3600


def test_no_wakeup_role_denied():
    out = run({"tool_name": "ScheduleWakeup", "tool_input": {"delaySeconds": 3600}}, CLAUDE_ROLE="quiet1", CADENCE_NO_WAKEUP_ROLES="quiet1")
    assert out["permissionDecision"] == "deny"


def test_override_file(tmp_path):
    f = tmp_path / "o.json"; f.write_text(json.dumps({"r1": 1200}))
    out = run({"tool_name": "ScheduleWakeup", "tool_input": {"delaySeconds": 60}}, CLAUDE_ROLE="r1", CADENCE_OVERRIDE_FILE=str(f))
    assert out["updatedInput"]["delaySeconds"] == 1200
