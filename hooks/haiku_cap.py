#!/usr/bin/env python3
"""haiku_cap.py: PreToolUse hook (no matcher, all tools), stops a Haiku subagent past 85K context.

Purpose: once a Haiku subagent's last prompt (input + cache read + cache creation tokens) passes 85,000, deny
  every further tool call with an order to answer now, so the agent ends below a price tier.
  Only subagents are checked (payload has agent_id); the transcript is agent_transcript_path, else
  <session transcript dir>/subagents/agent-<id>.jsonl.
  HAIKU_CAP env overrides the cap (tests).
Why: a long-context price tier on small models; the cap keeps subagents below it (set HAIKU_CAP).
Fire log: ~/.claude/hook-fires.log (utc, haiku_cap, deny: NK tokens agent id). Only denies are logged.
Removal condition: remove when Haiku pricing has it has no long-context tier, or subagents get a native max-context setting.
Fails open on any error. Exit 0 with a permissionDecision deny JSON on stdout when it fires.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hooklib import fire  # noqa: E402

CAP = int(os.environ.get("HAIKU_CAP") or 85_000)


def last_haiku_prompt(path, tail=512 << 10):
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - tail))
        lines = f.read().split(b"\n")
    for l in reversed(lines):
        if b'"usage"' not in l:
            continue
        try:
            m = json.loads(l).get("message") or {}
        except ValueError:
            continue
        u = m.get("usage")
        if not u:
            continue
        if "haiku" not in str(m.get("model", "")):
            return 0
        return u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
    return 0


def check(p):
    aid = str(p.get("agent_id") or "")
    if p.get("hook_event_name") != "PreToolUse" or not aid:
        return False
    path = p.get("agent_transcript_path") or ""
    if not path:
        tp = str(p.get("transcript_path") or "")
        path = os.path.join(tp[:-6] if tp.endswith(".jsonl") else tp, "subagents", "agent-%s.jsonl" % aid)
    n = last_haiku_prompt(path) if os.path.isfile(path) else 0
    if n < CAP:
        return False
    fire("haiku_cap", "deny: %dK tokens agent %s" % (n // 1000, aid[:12]))
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": (
            "haiku_cap: your context is %dK, the cap is %dK. "
            "Make no more tool calls. Reply now with your findings so far and a list of what is left "
            "undone, so the lead can hand the rest to a fresh agent." % (n // 1000, CAP // 1000))}}) + "\n")
    return True


if __name__ == "__main__":
    try:
        check(json.load(sys.stdin))
    except Exception:
        pass
    sys.exit(0)
