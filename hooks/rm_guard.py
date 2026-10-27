#!/usr/bin/env python3
"""PreToolUse hook: deny rm/rmdir forms that trigger Claude Code's built-in approval prompt.
Denies: glob, unguarded $var, command substitution, relative path, rm inside sh -c/eval,
xargs rm, find -exec rm. Allows literal absolute paths and "${VAR:?}" guarded forms.
Any internal error -> exit 0 silently."""
import sys, json, re, os

REASON = ("rm_guard: this rm would stall on Claude Code's built-in approval prompt (bypass cannot skip it). "
          "Rewrite: use literal absolute paths with no globs (e.g. rm -f /srv/app/tpl/a.txt), "
          "or guard variables as \"${VAR:?}\", or for globs/bulk use python3 -c 'import glob,os; "
          "[os.remove(p) for p in glob.glob(\"/abs/path/*\")]'. Never put rm inside sh -c.")

GUARD = re.compile(r"^\$\{\w+:\?[^}]*\}$")
HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
RMCMDS = {"rm", "rmdir"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
SKIPW = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "time", "nohup",
         "setsid", "command", "builtin", "exec", "rtk", "fi", "done"}
WRAP_VAL = {"-n", "-c", "-u", "-g", "-s", "-p", "-t", "-d", "-k"}
REDIR = re.compile(r"^\d*(?:>>?|<|&>>?|>&)(.*)$")
ASSIGN = re.compile(r"^[A-Za-z_]\w*\+?=")


def strip_heredocs(s):
    out, pend = [], []
    for line in s.split("\n"):
        if pend:
            if line.strip() == pend[0]:
                pend.pop(0)
            continue
        out.append(line)
        for m in HEREDOC.finditer(line):
            pend.append(m.group(2))
    return "\n".join(out)


def newword():
    return {"t": "", "glob": False, "dollar": False, "subst": False, "subs": [], "exists": False}


def match_paren(s, i):
    d = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            d += 1
        elif s[j] == ")":
            d -= 1
            if d == 0:
                return j
    return len(s) - 1


def dollar(s, i, w, inq):
    """handle $ at s[i]; return new index"""
    n = len(s)
    nx = s[i + 1] if i + 1 < n else ""
    if nx == "(":
        j = match_paren(s, i + 1)
        w["subst"] = True
        w["subs"].append(s[i + 2:j])
        return j + 1
    if nx == "{":
        j = s.find("}", i)
        j = n - 1 if j < 0 else j
        chunk = s[i:j + 1]
        if GUARD.match(chunk):
            w["t"] += chunk
        else:
            w["dollar"] = True
        return j + 1
    if nx and (nx.isalnum() or nx in "_?$@*!#-"):
        w["dollar"] = True
        j = i + 1
        while j < n and (s[j].isalnum() or s[j] == "_"):
            j += 1
        return max(j, i + 2)
    w["t"] += "$"
    return i + 1


def tokenize(s):
    toks, i, n, cur = [], 0, len(s), None

    def flush():
        nonlocal cur
        if cur is not None:
            toks.append(cur)
            cur = None

    while i < n:
        c = s[i]
        if c in " \t":
            flush(); i += 1; continue
        if c == "#" and cur is None:
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c in "\n;|()":
            flush(); toks.append(("op", c)); i += 1; continue
        if c == "&":
            prev = cur["t"][-1:] if cur else ""
            if prev in (">", "<") or s[i + 1:i + 2] == ">":
                if cur is None: cur = newword()
                cur["t"] += c; i += 1; continue
            flush(); toks.append(("op", c)); i += 1; continue
        if cur is None:
            cur = newword()
        cur["exists"] = True
        if c == "\\":
            if i + 1 < n and s[i + 1] != "\n":
                cur["t"] += s[i + 1]
            i += 2
        elif c == "'":
            j = s.find("'", i + 1); j = n if j < 0 else j
            cur["t"] += s[i + 1:j]; i = j + 1
        elif c == '"':
            i += 1
            while i < n and s[i] != '"':
                ch = s[i]
                if ch == "\\" and i + 1 < n:
                    cur["t"] += s[i + 1]; i += 2
                elif ch == "$":
                    i = dollar(s, i, cur, True)
                elif ch == "`":
                    j = s.find("`", i + 1); j = n if j < 0 else j
                    cur["subst"] = True; cur["subs"].append(s[i + 1:j]); i = j + 1
                else:
                    cur["t"] += ch; i += 1
            i += 1
        elif c == "`":
            j = s.find("`", i + 1); j = n if j < 0 else j
            cur["subst"] = True; cur["subs"].append(s[i + 1:j]); i = j + 1
        elif c == "$":
            i = dollar(s, i, cur, False)
        elif c in "*?[":
            cur["glob"] = True; cur["t"] += c; i += 1
        else:
            cur["t"] += c; i += 1
    flush()
    return toks


def segments(toks):
    seg = []
    for t in toks:
        if isinstance(t, tuple):
            if seg: yield seg
            seg = []
        else:
            seg.append(t)
    if seg: yield seg


def bad_target(w):
    if w["subst"] or w["dollar"] or w["glob"]:
        return True
    t = w["t"]
    return not (t.startswith("/") or t.startswith("~") or t.startswith("${"))


def check_rm(args, strict, xargs):
    if strict or xargs:
        return True
    opts_done = False
    skip = False
    for w in args:
        t = w["t"]
        if skip:
            skip = False; continue
        m = REDIR.match(t) if not (w["subst"] or w["glob"]) else None
        if m:
            skip = not m.group(1); continue
        if not opts_done and t == "--" and not w["glob"]:
            opts_done = True; continue
        if not opts_done and t.startswith("-") and len(t) > 1:
            continue
        if bad_target(w):
            return True
    return False


def check_seg(words, strict=False, xargs=False, depth=0):
    for w in words:                      # commands nested in $( ) / backticks
        for sub in w["subs"]:
            if scan(sub, strict, depth + 1):
                return True
    i, n = 0, len(words)
    while i < n:
        w = words[i]["t"]
        if ASSIGN.match(w):
            i += 1
        elif REDIR.match(w) and not words[i]["subst"]:
            i += 1 if REDIR.match(w).group(1) else 2
        elif w in SKIPW:
            i += 1
        elif w in ("sudo", "nice", "ionice", "env", "timeout", "taskset", "stdbuf"):
            i += 1; seen_c = False
            while i < n:
                x = words[i]["t"]
                if x.startswith("-") and len(x) > 1:
                    i += 1
                    if x in WRAP_VAL and i < n:
                        if x == "-c": seen_c = True
                        i += 1
                elif w == "env" and ASSIGN.match(x):
                    i += 1
                else:
                    break
            if w == "timeout" and i < n: i += 1
            if w == "taskset" and not seen_c and i < n: i += 1
        else:
            break
    if i >= n:
        return False
    cmd = os.path.basename(words[i]["t"].lstrip("\\"))
    rest = words[i + 1:]
    if cmd in RMCMDS:
        return check_rm(rest, strict, xargs)
    if cmd == "xargs":
        j = 0
        while j < len(rest) and rest[j]["t"].startswith("-") and len(rest[j]["t"]) > 1:
            x = rest[j]["t"]; j += 1
            if x in ("-I", "-n", "-P", "-d", "-L", "-E", "-s", "-a") and j < len(rest): j += 1
        return check_seg(rest[j:], strict, True, depth + 1) if j < len(rest) else False
    if cmd in SHELLS:
        for k, x in enumerate(rest):
            t = x["t"]
            if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", t) and k + 1 < len(rest):
                return scan(rest[k + 1]["t"], True, depth + 1)
        return False
    if cmd == "eval":
        return scan(" ".join(x["t"] for x in rest), True, depth + 1)
    if cmd == "find":
        for k, x in enumerate(rest):
            if x["t"] in ("-exec", "-execdir", "-ok", "-okdir") and k + 1 < len(rest):
                if os.path.basename(rest[k + 1]["t"]) in RMCMDS:
                    return True
    return False


def scan(cmd, strict=False, depth=0):
    if depth > 6:
        return False
    toks = tokenize(strip_heredocs(cmd))
    return any(check_seg(seg, strict, False, depth) for seg in segments(toks))


def main():
    try:
        d = json.load(sys.stdin)
        if d.get("tool_name") != "Bash":
            return 0
        cmd = (d.get("tool_input") or {}).get("command", "")
        if not isinstance(cmd, str) or "rm" not in cmd:
            return 0
        if scan(cmd):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                  "permissionDecision": "deny", "permissionDecisionReason": REASON}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
