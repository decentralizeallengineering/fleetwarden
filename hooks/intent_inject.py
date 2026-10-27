#!/usr/bin/env python3
"""SessionStart hook (startup, resume, clear, compact): inject standing instructions as context.

Injected, in order:
  1. $INTENT_FILE (default ~/.config/fleetwarden/intent.md): a one-page summary of what the operator wants, first 6 KB.
  2. The tail of the rules file: $RULES_FILE if set, else the first file named $RULES_NAME (default RULES.md) found in
     the session cwd or a parent folder below $HOME. Only the newest $RULES_CAP bytes (default 3000) are injected;
     a pointer to the full file is added when it is cut.
  3. Optional REMEMBER.md and STATE.md tails from the rules file's folder.
Unchanged context is not injected twice per session (state in $HOOK_STATE_DIR, default ~/.cache/fleetwarden), except after
a compaction. Nothing is injected when no rules file exists."""
import hashlib, json, os, sys, time

HOME = os.path.expanduser("~")
CAP = int(os.environ.get("RULES_CAP", "3000"))
STATE_DIR = os.environ.get("HOOK_STATE_DIR", os.path.join(HOME, ".cache", "fleetwarden"))
PRUNE = 7 * 86400


def changed(name, sid, text, reset=False):
    """True if text differs from the last emit for this session (records it). Fails open."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        p = os.path.join(STATE_DIR, name + ".json")
        try:
            st = json.load(open(p))
        except Exception:
            st = {}
        now = time.time()
        st = {k: v for k, v in st.items() if now - v.get("t", 0) < PRUNE}
        h = hashlib.sha256(text.encode()).hexdigest()
        new = reset or st.get(sid, {}).get("h") != h
        st[sid] = {"h": h, "t": now}
        tmp = p + ".tmp"
        json.dump(st, open(tmp, "w")); os.replace(tmp, p)
        return new
    except Exception:
        return True


def find(cwd):
    explicit = os.environ.get("RULES_FILE")
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    name = os.environ.get("RULES_NAME", "RULES.md")
    d = os.path.realpath(cwd)
    while d.startswith(HOME) and d != HOME:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
        d = os.path.dirname(d)
    return None


def tail(text, cap):
    b = text.encode()
    if len(b) <= cap:
        return text
    t = b[-cap:].decode("utf-8", "ignore")
    return t[t.find("\n") + 1:]


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    path = find(data.get("cwd") or os.getcwd())
    if not path:
        return 0
    text = open(path, encoding="utf-8", errors="replace").read()
    head = (f"STANDING RULES ({path}): the operator's own words. They override every other rule except safety. "
            "Newer entries override older ones.\n")
    if len(text.encode()) > CAP:
        cut = text.encode()[-CAP:].decode("utf-8", "ignore")
        cut = cut[cut.find("\n- ") + 1:] if "\n- " in cut else cut
        head += (f"File is {len(text.encode())//1024} KB; only the newest ~{CAP//1000} KB follows. "
                 f"Before acting on older topics, read the full file: {path}\n")
        text = cut
    # The tail alone can hide most of a long rules file, so a short intent summary rides first.
    intent = os.environ.get("INTENT_FILE") or os.path.join(HOME, ".config", "fleetwarden", "intent.md")
    if os.path.isfile(intent):
        head += "\n" + open(intent, encoding="utf-8", errors="replace").read()[:6_000] + "\n"
    folder = os.path.dirname(path)
    for name, cap, label in (("REMEMBER.md", 10_000, "durable facts, binding"),
                             ("STATE.md", 2_500, "newest handoff tail")):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            t = tail(open(p, encoding="utf-8", errors="replace").read(), cap)
            text += f"\n\n{name} ({p}; {label}):\n{t}"
    sid = str(data.get("session_id") or "")
    if sid and not changed("intent_inject", sid, head + text, reset=(data.get("source") == "compact")):
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                              "additionalContext": head + text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
