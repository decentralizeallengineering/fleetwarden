import os, subprocess

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "claude-session-up")


def test_help_exits_zero_without_side_effects():
    r = subprocess.run(["bash", BIN, "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "usage: claude-session-up" in r.stdout


def test_missing_env_file_fails(tmp_path):
    env = {**os.environ, "CLAUDE_SESSIONS_DIR": str(tmp_path)}
    r = subprocess.run(["bash", BIN, "nosuch"], capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode != 0
