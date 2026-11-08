#!/usr/bin/env python3
"""rm_catastrophe_guard.py: PreToolUse Bash hook (matcher "Bash"), blocks only catastrophic recursive rm.

Purpose: block `rm -r/-rf` on /, /home, $HOME, ~ itself (also their /* forms), on an unguarded
  bare $VAR target, or on an absolute path outside $HOME, /tmp, /var/tmp, /dev/shm and /run/user.
  Everything else passes; protected_set_guard.py keeps the protected set.
Why: guards must be narrow and logged; no unattended wipe of the machine.
Fire log: ~/.claude/hook-fires.log (utc, rm_catastrophe_guard, block: reason). Allows are not logged.
Removal condition: remove when protected_set_guard.py covers recursive rm outside the allowed roots, or when
  Claude Code ships an equivalent built-in rm deny rule.
Block = exit 2 + one stderr line. Internal error: block only if the raw text looks like a recursive rm.
"""
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hooklib import fire, read_input, strip_heredocs, deny  # noqa: E402

HOOK = "rm_catastrophe_guard"
HOME = os.path.expanduser("~")
# RM_GUARD_ALLOWED: colon-separated extra roots; default $HOME and the temp dirs
ALLOWED = [HOME, "/tmp", "/var/tmp", "/dev/shm", "/run/user"] + [p for p in os.environ.get("RM_GUARD_ALLOWED", "").split(":") if p]
NEVER = {"/", "/home", HOME}
RM = re.compile(r"(?:^|[;&|\n({`'\"]|\$\()\s*(?:(?:sudo(?:\s+-\S+)*|nohup|time|exec|command|env|setsid|rtk|[A-Za-z_]\w*=\S*)\s+)*"
                r"(?:\S*/)?rm\b([^;&|\n`)]*)")


def recursive(flags):
    return any(f == "--recursive" or (f.startswith("-") and not f.startswith("--") and re.search(r"[rR]", f)) for f in flags)


def judge(arg, cwd):
    a = arg.strip("'\"")
    if re.fullmatch(r"\$\{?\w+\}?/*\**", a) and ":?" not in a:
        return "unguarded bare variable target %r (use \"${VAR:?}\" or a literal path)" % arg
    if "$" in a:
        return None
    t = a.replace("~", HOME, 1) if a.startswith("~") else a
    glob_tail = t.endswith("/*") or t.endswith("/.*") or t.endswith("/.[!.]*")
    base = t[:t.rfind("/")] if glob_tail else t
    if not base.startswith("/"):
        base = os.path.join(cwd, base)
    p = os.path.normpath(base or "/")
    if p in NEVER:
        return "recursive rm on %s%s" % (p, "/*" if glob_tail else "")
    if not any(p == r or p.startswith(r + "/") for r in ALLOWED):
        return "recursive rm outside %s: %s (give an explicit path list under an allowed root)" % (", ".join(ALLOWED[:3]), p)
    return None


def decide(cmd, cwd):
    for m in RM.finditer(strip_heredocs(cmd)):
        try:
            toks = shlex.split(m.group(1))
        except ValueError:
            toks = m.group(1).split()
        flags = [t for t in toks if t.startswith("-") and t != "--"]
        paths = [t for t in toks if not t.startswith("-") or t == "-"]
        if "--" in toks:
            paths = toks[toks.index("--") + 1:]
        if not recursive(flags):
            continue
        for p in paths:
            why = judge(p, cwd)
            if why:
                return why
    return None


def main():
    d = read_input()
    if d.get("tool_name") not in (None, "Bash"):
        return
    cmd = (d.get("tool_input") or {}).get("command", "")
    cwd = d.get("cwd") or os.getcwd()
    try:
        why = decide(cmd, cwd)
    except Exception:
        if re.search(r"\brm\s+(-\S*\s+)*-\S*[rR]", cmd):
            deny(HOOK, "internal error while checking a recursive rm; rewrite it with a literal path")
        return
    if why:
        deny(HOOK, why)


if __name__ == "__main__":
    main()
