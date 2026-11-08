"""Tests for rm_catastrophe_guard, hang_guard, kill_guard, haiku_cap. Run: python3 -m unittest -q test_guards"""
import json, os, subprocess, sys, tempfile, unittest

D = os.path.dirname(os.path.abspath(__file__))


def run(hook, payload, **env):
    e = dict(os.environ, HOOK_FIRES_LOG=os.path.join(tempfile.gettempdir(), "fw-test-fires.log"), **env)
    return subprocess.run([sys.executable, os.path.join(D, hook)], input=json.dumps(payload),
                          capture_output=True, text=True, env=e, timeout=10)


def bash(cmd, **kw):
    return dict({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "/tmp"}, **kw)


class RmCatastrophe(unittest.TestCase):
    def test_blocks(self):
        for c in ["rm -rf /", "rm -rf ~", "rm -rf ~/*", "rm -rf $DIR", "sudo rm -rf /etc/x", "rm -r /usr/local/lib"]:
            self.assertEqual(run("rm_catastrophe_guard.py", bash(c)).returncode, 2, c)

    def test_allows(self):
        for c in ["rm -rf /tmp/build", "rm -f /tmp/a.txt", 'rm -rf "${D:?}/x"', "ls -la", "echo rm -rf /"]:
            self.assertEqual(run("rm_catastrophe_guard.py", bash(c)).returncode, 0, c)


class Hang(unittest.TestCase):
    def test_blocks(self):
        for c in ["sleep 1000", "tail -f /var/log/syslog", "watch ls", "vim x"]:
            self.assertEqual(run("hang_guard.py", bash(c)).returncode, 2, c)

    def test_allows(self):
        for c in ["sleep 5", "timeout 20 tail -f x", "tail -n 20 x", "echo 'sleep 1000'"]:
            self.assertEqual(run("hang_guard.py", bash(c)).returncode, 0, c)


class Kill(unittest.TestCase):
    def test_pid1(self):
        self.assertEqual(run("kill_guard.py", bash("kill -9 1")).returncode, 2)

    def test_allows_unknown_pid(self):
        self.assertEqual(run("kill_guard.py", bash("kill 999999")).returncode, 0)


class HaikuCap(unittest.TestCase):
    def test_denies_over_cap(self):
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "s", "subagents"); os.makedirs(sub)
            tp = os.path.join(sub, "agent-abc.jsonl")
            open(tp, "w").write(json.dumps({"message": {"model": "claude-haiku-x", "usage": {"input_tokens": 90000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}) + "\n")
            r = run("haiku_cap.py", {"hook_event_name": "PreToolUse", "agent_id": "abc", "agent_transcript_path": tp, "transcript_path": d + "/s.jsonl"})
            self.assertEqual(r.returncode, 0)
            self.assertIn("deny", r.stdout)

    def test_silent_for_main_session(self):
        self.assertEqual(run("haiku_cap.py", {"tool_name": "Bash"}).stdout, "")


if __name__ == "__main__":
    unittest.main()
