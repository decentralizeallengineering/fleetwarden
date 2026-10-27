#!/usr/bin/env python3
"""PreToolUse Bash hook: block shell waits that can hang forever.
Rule 1: pgrep/pkill -f whose pattern self-matches the bash command line (no [x] bracket trick).
Rule 2: until/while loop containing sleep with no bound (timeout, counter, seq, $SECONDS, date deadline, break).
run_in_background skips rule 2 only. Block = exit 2 + one-line stderr. Any internal error = exit 0."""
import sys, json, re

HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
PG = re.compile(r"""\b(pgrep|pkill)\b((?:\s+-[A-Za-z0-9]+)*)\s+(?:--\s+)?("[^"]*"|'[^']*'|[^\s;&|)<>]+)""")
LOOP = re.compile(r"""(?:^|[;&|(){\n]|\bthen\b|\bdo\b|\belse\b|-c\s+['"]|\$\()\s*(until|while)\b""")
BOUND = [
    re.compile(r"\btimeout\s+(?:-\S+\s+)*\d"),
    re.compile(r"\$\{?SECONDS\b"),
    re.compile(r"\bseq\s+\d"),
    re.compile(r"\{\d+\.\.\d+\}"),
    re.compile(r"\bdate\s+\+?%?s|\bdate\s+-d|\$\(date\b|`date\b"),
    re.compile(r"\bbreak\b"),
    re.compile(r"\bkill\s+-0\b"),
    re.compile(r"\(\(\s*\+*\w+\s*\+\+|\+\+\s*\w+|\w+\s*\+=|\w+\+\+|\$\(\(\s*\w+\s*\+\s*\d|\blet\b"),
    re.compile(r"\s-(?:lt|le|gt|ge)\s"),
    re.compile(r"\bfor\s*\(\("),
]

def strip_heredocs(cmd):
    return HEREDOC.sub("", cmd)

def rule1(cmd, in_loop):
    for m in PG.finditer(cmd):
        tool, flags, pat = m.group(1), m.group(2), m.group(3)
        if not re.search(r"-\w*f", flags or ""):
            continue
        p = pat.strip("'\"")
        if not p or "[" in p and "]" in p:
            continue
        # self-match matters when result gates a wait (loop/condition) or pkill would kill own shell
        occurrences = cmd.count(p)
        gated = in_loop or bool(re.search(r"(?:\b(?:until|while)\b|!)\s*$", cmd[:m.start()])) 
        if tool == "pkill" and occurrences >= 1 and re.search(r"\bpkill\b", cmd) and occurrences >= 2:
            return ("pkill -f %r also appears in this command line, so it kills its own shell: use the bracket trick "
                    "(pkill -f '[%s]%s'), or kill by PID" % (p, p[:1], p[1:]))
        if tool == "pgrep" and gated:
            return ("pgrep -f %r matches this bash command line itself and never goes false: wait on a PID with "
                    "`while kill -0 PID`, on a file, or use the bracket trick '[%s]%s'" % (p, p[:1], p[1:]))
    return None

def decide(cmd, background=False):
    if not cmd or not isinstance(cmd, str):
        return None
    c = strip_heredocs(cmd)
    loop = LOOP.search(c) is not None
    r = rule1(c, loop and "sleep" in c)
    if r:
        return r
    if background or not loop or not re.search(r"\bsleep\b", c):
        return None
    if any(b.search(c) for b in BOUND):
        return None
    return ("unbounded until/while-sleep loop can hang the turn forever: wrap it as `timeout 600 bash -c '...'` "
            "or add a counter (for i in $(seq 1 120); do ... && break; sleep 5; done)")

def main():
    try:
        d = json.load(sys.stdin)
        if d.get("tool_name") not in (None, "Bash"):
            return 0
        ti = d.get("tool_input") or {}
        why = decide(ti.get("command", ""), bool(ti.get("run_in_background")))
    except Exception:
        return 0
    if why:
        sys.stderr.write("wait_guard: " + why.replace("\n", " ") + "\n")
        return 2
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
