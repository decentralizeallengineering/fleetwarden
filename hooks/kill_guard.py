#!/usr/bin/env python3
"""kill_guard.py: PreToolUse Bash hook (matcher "Bash"), blocks kill/pkill/killall that would hit infrastructure.

Purpose: block a kill that resolves to pid 1 (or -1/0 = everything), a claude process, the tmux server,
  sshd, systemd*, or a qemu process whose VM name (-name) is not written in the command.
  Killing specific pids of other processes is allowed. pkill/killall are resolved with pgrep first.
Why: a stray pkill must not take down the sessions or the VMs that run the fleet.
Fire log: ~/.claude/hook-fires.log (utc, kill_guard, block: reason). Allows are not logged.
Removal condition: remove when sessions run in a cgroup/container where such a kill cannot reach hosts services.
Block = exit 2 + one stderr line. Unresolvable $VAR pids are allowed (narrow guard).
"""
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hooklib import read_input, strip_heredocs, deny  # noqa: E402

HOOK = "kill_guard"
KILL = re.compile(r"(?:^|[;&|\n({`'\"]|\$\()\s*(?:(?:sudo(?:\s+-\S+)*|nohup|time|exec|command|env|setsid|rtk|timeout\s+\d+\S*|[A-Za-z_]\w*=\S*)\s+)*"
                  r"(?:\S*/)?(kill|pkill|killall)\b([^;&|\n]*)")
SIGFLAG = re.compile(r"^-(\d+|[A-Z][A-Z0-9]*|SIG[A-Z0-9]+|s|n|signal)$")
PROT_WORDS = re.compile(r"claude|tmux|sshd|systemd|qemu")


def proc(pid):
    try:
        comm = open("/proc/%d/comm" % pid).read().strip()
        cmdline = open("/proc/%d/cmdline" % pid, "rb").read().split(b"\0")
        cmdline = [c.decode("utf-8", "replace") for c in cmdline if c]
        try:
            exe = os.readlink("/proc/%d/exe" % pid)
        except OSError:
            exe = ""
        return comm, cmdline, exe
    except OSError:
        return None


def protected(pid, cmdtext):
    if pid == 1:
        return "pid 1"
    p = proc(pid)
    if not p:
        return None
    comm, argv, exe = p
    a0 = os.path.basename(argv[0]) if argv else ""
    if comm == "claude" or a0 == "claude" or "/claude/versions/" in exe or os.path.basename(exe) == "claude":
        return "pid %d is a claude process" % pid
    if comm.startswith("tmux") and "server" in comm:
        return "pid %d is the tmux server" % pid
    if comm == "tmux" and not any(x in argv[1:2] for x in ("-c",)) and a0.startswith("tmux:"):
        return "pid %d is the tmux server" % pid
    if comm == "sshd" or a0.startswith("sshd"):
        return "pid %d is sshd" % pid
    if comm.startswith("systemd"):
        return "pid %d is %s" % (pid, comm)
    if comm.startswith("qemu"):
        name = None
        for i, x in enumerate(argv):
            if x == "-name" and i + 1 < len(argv):
                name = re.sub(r"^guest=", "", argv[i + 1]).split(",")[0]
        if not name or name not in cmdtext:
            return "pid %d is qemu VM %s, which is not named in the command" % (pid, name or "?")
    return None


def pgrep(flags, pats):
    try:
        r = subprocess.run(["pgrep"] + flags + ["--"] + pats, capture_output=True, text=True, timeout=5)
        return [int(x) for x in r.stdout.split() if int(x) not in (os.getpid(), os.getppid())]
    except Exception:
        return []


def decide(cmd):
    c = strip_heredocs(cmd)
    for m in KILL.finditer(c):
        verb, rest = m.group(1), m.group(2)
        try:
            toks = shlex.split(rest)
        except ValueError:
            toks = rest.split()
        if ("$(" in rest or "`" in rest) and PROT_WORDS.search(rest):
            return "%s with a substitution that selects a protected process: %s" % (verb, rest.strip())
        if verb == "kill":
            if toks and toks[0] == "-1" or (len(toks) > 1 and toks[0] in ("-9", "-KILL", "-TERM") and toks[1] == "-1"):
                return "kill -1 would signal every process"
            args = [t for t in toks if not SIGFLAG.match(t) and t not in ("-l", "-L", "--")]
            for t in args:
                if re.fullmatch(r"-?\d+", t):
                    n = int(t)
                    if n in (0, -1):
                        return "kill of pid %s signals a whole process group / everything" % t
                    why = protected(abs(n), c)
                    if why:
                        return why
                elif "$(" in t or "`" in t:
                    if PROT_WORDS.search(t):
                        return "kill with a substitution that selects a protected process: %s" % t
        else:
            flags, pats, skip = [], [], False
            for t in toks:
                if skip:
                    flags.append(t); skip = False; continue
                if SIGFLAG.match(t):
                    continue
                if t.startswith("-"):
                    flags.append(t)
                    if t in ("-u", "-U", "-g", "-G", "-P", "-t", "-s", "-n", "-o"):
                        skip = True
                else:
                    pats.append(t)
            if verb == "killall":
                flags = ["-x"]
            if not pats and not flags:
                continue
            for pid in pgrep(flags, pats):
                why = protected(pid, c)
                if why:
                    return "%s %s would match: %s" % (verb, " ".join(pats), why)
    return None


def main():
    d = read_input()
    if d.get("tool_name") not in (None, "Bash"):
        return
    cmd = (d.get("tool_input") or {}).get("command", "")
    try:
        why = decide(cmd)
    except Exception:
        if re.search(r"\b(pkill|killall)\b[^\n]*(claude|tmux|sshd|systemd|qemu)", cmd) or re.search(r"\bkill\s+(-\S+\s+)*1\b", cmd):
            deny(HOOK, "internal error while checking a kill of a protected process")
        return
    if why:
        deny(HOOK, why)


if __name__ == "__main__":
    main()
