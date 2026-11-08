#!/usr/bin/env python3
"""rtk_widen.py: PreToolUse Bash hook (matcher "Bash"), rewrites commands through rtk via updatedInput.

Purpose: when the `rtk` binary works (`rtk gain`), rewrite pipeline-first segments that `rtk rewrite` supports
  (git, ls, grep, find, gh, docker, cargo, pytest, journalctl, ...) so output is filtered; pipe- and exit-code-safe,
  falls back to `rtk hook claude` for heredocs/subshells/loops.
Why: token waste; the stock rtk hook wraps only a small share of Bash calls.
Fire log: ~/.claude/hook-fires.log (utc, rtk_widen, rewrite: old => new). Only rewrites are logged.
Removal condition: remove when `rtk hook claude` covers the same commands, or rtk is uninstalled (it then no-ops).
Skips silently when rtk is absent or broken. Never blocks (always exit 0).
"""
import json, os, re, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hooklib import fire  # noqa: E402

RTK = shutil.which("rtk") or os.path.expanduser("~/.local/bin/rtk")
SAFE_FOLLOW = {"head", "tail"}
WHOLE_ONLY = {"ls", "find", "ps", "tree", "wc", "diff"}
PREFIXES = [
    re.compile(r"timeout\s+(?:-[-\w=]+\s+)*\d+[smhd]?\s+"),
    re.compile(r"nice\s+(?:-n\s*\d+\s+)?"),
    re.compile(r"taskset\s+(?:-c\s+)?[\d,-]+\s+"),
    re.compile(r"time\s+"),
]
REDIR = re.compile(r"(?<![\d&])[<>]|\d>(?!&)|&>")  # any redirect except N>&M
OKREDIR = re.compile(r"\d?>\s*/dev/null|&>\s*/dev/null|\d>&\d")
LOOPKW = {"for", "while", "until", "if", "case", "function", "select", "do", "then", "done", "fi"}


STATE = {"ask": False}


class Bail(Exception):
    pass


def split(cmd):
    """-> list of [text, op_after]; raises Bail on constructs we do not parse."""
    segs, cur, i, n, q = [], "", 0, len(cmd), None
    while i < n:
        c = cmd[i]
        if q:
            cur += c
            if c == "\\" and q == '"' and i + 1 < n:
                cur += cmd[i + 1]; i += 1
            elif c == q:
                q = None
            elif q == '"' and (c == "`" or cmd[i:i + 2] == "$("):
                raise Bail
            i += 1; continue
        if c == "\\":
            if i + 1 < n and cmd[i + 1] == "\n":
                raise Bail
            cur += cmd[i:i + 2]; i += 2; continue
        if c in "'\"":
            q = c; cur += c; i += 1; continue
        if c == "`" or c in "(){}" or cmd[i:i + 2] in ("<<", "$(", "<("):
            raise Bail
        two = cmd[i:i + 2]
        if two in ("&&", "||", "|&"):
            segs.append([cur, two]); cur = ""; i += 2; continue
        if c == "&" and (cur.endswith((">", "<")) or cmd[i + 1:i + 2] == ">"):
            cur += c; i += 1; continue
        if c in ";|&\n":
            segs.append([cur, c]); cur = ""; i += 1; continue
        cur += c; i += 1
    if q:
        raise Bail
    segs.append([cur, ""])
    return segs


def rtk_rewrite(core):
    try:
        r = subprocess.run([RTK, "rewrite", core], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    out = r.stdout.strip()
    if r.returncode == 2:  # deny rule: let rtk's own hook handle the whole command
        raise Bail
    if r.returncode == 3:  # rewrite, but permission stays "ask" (no auto-allow)
        STATE["ask"] = True
    return out if r.returncode in (0, 3) and out and out != core else None


def rewrite_core(core):
    if core.startswith("rtk "):
        return None
    if re.match(r"journalctl\b", core):
        return "rtk " + core
    m = re.match(r"tail\s+([A-Za-z0-9_./~][^\s|;&<>*?$'\"]*)$", core)
    if m:
        return f"rtk read {m.group(1)} --tail-lines 10"
    r = rtk_rewrite(core)
    if r:
        return r
    if re.match(r"\S*/(?:python[\d.]*\s+-m\s+pytest|pytest)\b", core):
        return "rtk test " + core
    return None


def rewrite_segment(text, ok_follow, whole, op):
    core = text.strip()
    if not core or core.split()[0] in LOOPKW:
        return text
    lead = text[:len(text) - len(text.lstrip())]; trail = text[len(text.rstrip()):]
    stripped = OKREDIR.sub("", core)
    if REDIR.search(stripped):
        return text
    prefix = ""
    while True:
        for p in PREFIXES:
            m = p.match(core)
            if m:
                prefix += m.group(0); core = core[m.end():]; break
        else:
            break
    if not core:
        return text
    word = os.path.basename(core.split()[0])
    if word in WHOLE_ONLY and not whole:
        return text
    if not ok_follow:
        return text
    if word == "find" and op in ("&&", "||"):
        return text
    new = rewrite_core(core)
    return text if not new else lead + prefix + new + trail


def widen(cmd):
    segs = split(cmd)
    # pipelines: split on non-pipe ops
    out, i = [], 0
    while i < len(segs):
        j = i
        while j < len(segs) and segs[j][1] in ("|", "|&"):
            j += 1
        pipe = segs[i:j + 1]
        first_text, last_op = pipe[0][0], pipe[-1][1]
        followers = [s[0].strip().split()[0] if s[0].strip() else "" for s in pipe[1:]]
        ok = all(f in SAFE_FOLLOW for f in followers)
        whole = len(pipe) == 1
        pipe[0][0] = rewrite_segment(first_text, ok, whole, last_op)
        out.extend(pipe)
        i = j + 1
    return "".join(t + o for t, o in out)


def main():
    raw = sys.stdin.read()
    if not os.path.exists(RTK):
        return
    try:
        if subprocess.run([RTK, "gain"], capture_output=True, timeout=10).returncode != 0:
            return
    except Exception:
        return
    try:
        d = json.loads(raw)
        ti = d.get("tool_input") or {}
        cmd = ti.get("command", "")
        if d.get("tool_name") != "Bash" or not cmd:
            return
    except Exception:
        return
    try:
        new = widen(cmd)
    except Bail:
        r = subprocess.run([RTK, "hook", "claude"], input=raw, capture_output=True, text=True)
        sys.stdout.write(r.stdout); return
    except Exception:
        return
    if new != cmd:
        fire("rtk_widen", "rewrite: %s => %s" % (cmd[:80], new[:120]))
        ti = dict(ti); ti["command"] = new
        h = {"hookEventName": "PreToolUse", "permissionDecisionReason": "RTK auto-rewrite (widened)",
             "updatedInput": ti}
        if not STATE["ask"]:
            h["permissionDecision"] = "allow"
        print(json.dumps({"hookSpecificOutput": h}))


if __name__ == "__main__":
    main()
