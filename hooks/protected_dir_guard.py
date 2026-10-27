#!/usr/bin/env python3
"""PreToolUse hook: keep one directory read-only for ordinary sessions.

$PROTECTED_DIR        the directory to protect (absolute). Unset: the hook does nothing.
$PROTECTED_DIR_ALLOW  comma separated paths relative to it that stay writable (default "PROPOSALS.md,drafts/").
$PROTECTED_DIR_ADMIN  comma separated roles that may edit it (default none). Role: _role.py.
Write/Edit/NotebookEdit on the tree and Bash commands that write to it are denied; read-only Bash is allowed.
Fail-open."""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _role import role as session_role

NULLREDIR = re.compile(r"\d?>&\d|&?\d?>\s*/dev/null")
WRITE = re.compile(r"(>|\btee\b|\bsed\s+-i|\bmv\b|\bcp\b|\brm\b|\bchmod\b|\btruncate\b|\bln\b|\binstall\b|open\()")


def config():
    root = os.environ.get("PROTECTED_DIR", "").rstrip("/")
    allow = [a.strip() for a in os.environ.get("PROTECTED_DIR_ALLOW", "PROPOSALS.md,drafts/").split(",") if a.strip()]
    admins = {a.strip() for a in os.environ.get("PROTECTED_DIR_ADMIN", "").split(",") if a.strip()}
    return root, tuple(root + "/" + a for a in allow), admins


def main():
    root, ok, admins = config()
    if not root:
        return 0
    d = json.load(sys.stdin)
    if session_role(d) in admins:
        return 0
    ti = d.get("tool_input") or {}
    if d.get("tool_name") == "Bash":
        # Judge each command segment alone: a read-only `cat DIR/x 2>/dev/null`, or a mention of the directory
        # beside an unrelated `>` elsewhere in the command, must not be denied.
        pat = re.escape(root) + r"[^\s'\";|&)]*"
        for seg in re.split(r"[;\n|&]+", NULLREDIR.sub(" ", ti.get("command", ""))):
            hits = [m for m in re.findall(pat, seg) if not m.startswith(ok)]
            if hits and WRITE.search(seg):
                break
        else:
            return 0
    else:
        p = ti.get("file_path") or ti.get("notebook_path") or ""
        if not (p == root or p.startswith(root + "/")) or p.startswith(ok):
            return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "protected_dir_guard: %s is read-only for this session. Write under one of: %s." % (root, ", ".join(ok) or "(nothing)")}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
