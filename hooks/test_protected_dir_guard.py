import json, os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__))
D = "/data/protected"


def run(payload, **env):
    e = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_ROLE", "TMUX", "PROTECTED_DIR", "PROTECTED_DIR_ADMIN", "PROTECTED_DIR_ALLOW")}
    e["PROTECTED_DIR"] = D
    e.update(env)
    r = subprocess.run([sys.executable, H + "/protected_dir_guard.py"], input=json.dumps(payload), capture_output=True, text=True, env=e)
    return r.stdout


W = {"tool_name": "Write", "tool_input": {"file_path": D + "/README.md"}}


def test_unset_dir_does_nothing():
    e = {k: v for k, v in os.environ.items() if k != "PROTECTED_DIR"}
    r = subprocess.run([sys.executable, H + "/protected_dir_guard.py"], input=json.dumps(W), capture_output=True, text=True, env=e)
    assert r.stdout == ""


def test_fake_tmux_is_not_admin():
    # The role comes from the session (_role.py), never from $TMUX.
    assert "deny" in run(W, PROTECTED_DIR_ADMIN="boss", TMUX="/tmp/tmux-1000/boss,1,0")


def test_admin_role_allowed():
    assert run({**W, "transcript_path": "/home/u/.claude/projects/-home-u-boss/x.jsonl"}, PROTECTED_DIR_ADMIN="boss") == ""
    assert run(W, CLAUDE_ROLE="boss", PROTECTED_DIR_ADMIN="boss") == ""


def test_allowed_paths_ok():
    assert run({"tool_name": "Write", "tool_input": {"file_path": D + "/drafts/x.md"}}) == ""
    assert run({"tool_name": "Write", "tool_input": {"file_path": "/data/protected-other/x.md"}}) == ""


def _bash(cmd):
    return run({"tool_name": "Bash", "tool_input": {"command": cmd}})


def test_bash_read_only_allowed():
    assert _bash("cat %s/*.md 2>/dev/null | wc -w" % D) == ""
    assert _bash("grep -r x %s 2>&1 | head" % D) == ""
    assert _bash("echo x > /tmp/a.txt; ls %s | head" % D) == ""


def test_bash_write_denied():
    assert "deny" in _bash("echo x > %s/README.md" % D)
    assert "deny" in _bash("sed -i s/a/b/ %s/README.md 2>/dev/null" % D)
    assert "deny" in _bash("ls /tmp && rm %s/README.md" % D)
    assert "deny" in _bash("cat x 2>&1 > %s/README.md" % D)
    assert _bash("echo x >> %s/PROPOSALS.md" % D) == ""
