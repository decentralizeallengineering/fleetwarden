#!/usr/bin/env python3
import sys, os, json, glob, subprocess, unittest, time
sys.path.insert(0, os.path.dirname(__file__))
import wait_guard as w

BLOCK = [
 'until [ -n "$(grep -c base /tmp/x/tasks/bx5nckmzx.output)" ] && ! pgrep -f "test_gate_sh.py" >/dev/null; do sleep 5; done; cat /tmp/x',
 'until curl -s http://localhost:8080/health; do sleep 2; done',
 'while true; do sleep 5; done',
 'while ! pgrep -f test_gate >/dev/null; do sleep 1; done',
 'while pgrep -f "train.py" >/dev/null; do sleep 10; done; echo done',
 "bash -c 'until [ -f /tmp/f ]; do sleep 5; done'",
 'until [ -f /tmp/done ]; do\n  sleep 3\ndone\necho ok',
 'pkill -f "foo.py"; echo "restart foo.py"',
 'echo start && while [ ! -s out.log ]; do sleep 1; done',
 'if pgrep -f "worker.py" >/dev/null; then echo up; fi; until pgrep -f "worker.py"; do sleep 1; done',
]
ALLOW = [
 'git status', 'git commit -m "wait until done while testing"', 'pytest -x tests/', 'ls -la /tmp',
 'while read l; do echo "$l"; done < /tmp/f',
 "timeout 300 bash -c 'until [ -f /tmp/f ]; do sleep 5; done'",
 'for i in $(seq 1 60); do [ -f /tmp/f ] && break; sleep 5; done',
 'for f in *.txt; do echo $f; sleep 1; done',
 'pgrep -f "[t]est_gate"',
 'until [ -f /tmp/f ] || [ $SECONDS -gt 600 ]; do sleep 5; done',
 'n=0; until [ -f /tmp/f ] || [ $n -ge 60 ]; do n=$((n+1)); sleep 5; done',
 'end=$(( $(date +%s) + 300 )); while [ $(date +%s) -lt $end ]; do sleep 2; done',
 "cat <<'EOF'\nwait until the server is up\nwhile true; do sleep 1; done\nEOF",
 'echo "we poll until ready, while sleeping 5"',
 "python3 -c 'print(\"until done\")'",
 'sleep 30', 'pgrep -f "[t]est_gate" && echo running',
 'ps aux | grep -c python', 'kill -0 1234 && echo alive',
 'while kill -0 1234 2>/dev/null; do sleep 2; done; echo gone;  # bound by pid exit is still unbounded' ,
]
BG_ALLOW = ['until [ -f /tmp/f ]; do sleep 5; done']
BG_BLOCK = ['until pgrep -f "x.py" >/dev/null; do sleep 5; done']

class T(unittest.TestCase):
    pass
def mk(cmd, bg, expect):
    def t(self):
        r = w.decide(cmd, bg)
        self.assertEqual(r is not None, expect, (cmd, r))
    return t
cases = [(c, False, True) for c in BLOCK] + [(c, False, False) for c in ALLOW[:-1]] + \
        [(c, True, False) for c in BG_ALLOW] + [(c, True, True) for c in BG_BLOCK]
# while kill -0 PID loop: user-suggested fix must not be blocked
cases.append(('while kill -0 1234 2>/dev/null; do sleep 2; done; echo gone', False, False))
for i, (c, bg, e) in enumerate(cases):
    setattr(T, "test_%02d_%s" % (i, "block" if e else "allow"), mk(c, bg, e))

class Proc(unittest.TestCase):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wait_guard.py")
    def run_h(self, stdin):
        t = time.time()
        p = subprocess.run([sys.executable, self.path], input=stdin, capture_output=True, text=True)
        return p, time.time() - t
    def test_block_exit2(self):
        p, dt = self.run_h(json.dumps({"tool_name": "Bash", "tool_input": {"command": BLOCK[0]}}))
        self.assertEqual(p.returncode, 2); self.assertEqual(len(p.stderr.strip().splitlines()), 1); self.assertLess(dt, 0.1)
    def test_allow_silent(self):
        p, _ = self.run_h(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}}))
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
    def test_garbage_exit0(self):
        for s in ["", "not json", "[]", '{"tool_input": 5}', "null"]:
            self.assertEqual(self.run_h(s)[0].returncode, 0, s)

def replay(d):
    """Replay Bash commands from the newest transcripts in directory d through the guard and list the blocked ones."""
    files = sorted(glob.glob(os.path.join(d, "*.jsonl")), key=os.path.getmtime)[-5:]
    cmds = []
    for f in files:
        for line in open(f, errors="replace"):
            if '"Bash"' not in line: continue
            try: o = json.loads(line)
            except Exception: continue
            m = o.get("message") or {}
            c = m.get("content")
            if not isinstance(c, list): continue
            for b in c:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                    ti = b.get("input") or {}
                    cmds.append((ti.get("command", ""), bool(ti.get("run_in_background"))))
    blocked = [(c, w.decide(c, bg)) for c, bg in cmds if w.decide(c, bg)]
    print("REPLAY total=%d blocked=%d" % (len(cmds), len(blocked)))
    for c, r in blocked:
        print("  BLOCKED:", c[:150].replace("\n", "\\n"))

if __name__ == "__main__":
    if "--replay" in sys.argv:
        replay(sys.argv[sys.argv.index("--replay") + 1])  # directory of transcript .jsonl files
    else:
        unittest.main(verbosity=1)
