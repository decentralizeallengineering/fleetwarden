#!/usr/bin/env python3
"""hang_guard.py: PreToolUse Bash hook (matcher "Bash"), blocks shell commands that can hang a turn forever.

Purpose: block bare `sleep` over 300 s, `tail -f/-F/--follow`, `watch`, builtin `wait`, interactive
  editors/pagers (vim vi nano emacs less more pico htop) and `top` without -b, unless the command carries
  `timeout N` or the call has run_in_background. Words inside quotes/heredocs are ignored.
Why: a turn blocked on an endless wait leaves the session dead and unwatched.
Fire log: ~/.claude/hook-fires.log (utc, hang_guard, block: reason). Allows are not logged.
Removal condition: remove when Claude Code enforces a tool timeout that kills such commands on its own.
Block = exit 2 + one stderr line. Internal error: allow (this guard only blocks matched patterns).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hooklib import read_input, strip_quotes, deny, POS  # noqa: E402

HOOK = "hang_guard"
EDITORS = r"(?:vim|vi|nvim|nano|emacs|pico|less|more|htop)"


def secs(tok):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", tok)
    if not m:
        return 0
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def decide(cmd, background):
    c = strip_quotes(cmd)
    bounded = re.search(r"\btimeout\s+(?:-\S+\s+)*\d", c)
    for m in re.finditer(POS + r"sleep((?:\s+[\d.]+[smhd]?)+)", c):
        total = sum(secs(t) for t in m.group(1).split())
        if total > 300 and not background:
            return "sleep %ds blocks the turn longer than 300 s; use run_in_background or a bounded loop" % total
    if background or bounded:
        return None
    if re.search(POS + r"tail\b[^;&|\n]*?\s(?:-[A-Za-z]*[fF][A-Za-z]*|--follow\b)", c):
        return "tail -f never returns; use `timeout N tail -f ...`, or read the last lines with tail -n"
    if re.search(POS + r"watch\b", c):
        return "watch never returns; run the command once, or `timeout N watch ...`"
    if re.search(POS + r"wait\b(?!-)", c):
        return "bare `wait` has no timeout; wait on a PID with `timeout N tail --pid=PID -f /dev/null` or poll with a bound"
    if re.search(POS + EDITORS + r"\b", c):
        return "interactive editor/pager has no tty here; use cat, sed -n, head or Read/Edit tools"
    for m in re.finditer(POS + r"top\b([^;&|\n]*)", c):
        if not re.search(r"\s-[A-Za-z]*b", m.group(1)):
            return "`top` without -b is interactive; use `top -b -n 1`"
    return None


def main():
    d = read_input()
    if d.get("tool_name") not in (None, "Bash"):
        return
    ti = d.get("tool_input") or {}
    try:
        why = decide(ti.get("command", ""), bool(ti.get("run_in_background")))
    except Exception:
        return
    if why:
        deny(HOOK, why)


if __name__ == "__main__":
    main()
