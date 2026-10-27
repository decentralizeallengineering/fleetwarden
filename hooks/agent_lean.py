#!/usr/bin/env python3
"""PreToolUse Agent hook: rewrite subagent_type general-purpose, chore or unset to a lean agent type, because the
default agent carries a much larger base prompt. Edits go to $AGENT_LEAN_BUILD (default lean-build), read-only work to
$AGENT_LEAN_SEARCH (default lean-search); both must exist as agent definitions (see examples/agents). Kept as is when the
prompt needs web, MCP, skills or a browser. Analysis-style read-only prompts also get model $AGENT_LEAN_ANALYSIS_MODEL
(default sonnet). Fail-open. Kill switch: COST_STACK_OFF=1."""
import json, os, re, sys

HEAVY = {None, "", "general-purpose", "chore"}
NEEDS_FULL = re.compile(r"https?://|\bweb\b(?!/)|\bWebFetch\b|\bWebSearch\b|\bmcp__|\bMCP\b|\bskill\b|\bbrowser\b|\bonline\b|\binternet\b", re.I)
LEAN_BUILD = os.environ.get("AGENT_LEAN_BUILD", "lean-build")
LEAN_SEARCH = os.environ.get("AGENT_LEAN_SEARCH", "lean-search")
ANALYSIS_MODEL = os.environ.get("AGENT_LEAN_ANALYSIS_MODEL", "sonnet")
# Read-only audits and root-cause work need a stronger model than a plain search.
ANALYSIS = re.compile(r"\b(?:audit|analy[sz]e|analysis|investigat\w*|root cause|trace|diagnos\w*|review|why)\b", re.I)
BUILDS = re.compile(r"\b(?:edit|write|fix|implement|build|create|add|change|refactor|commit|patch|install|update|rename|delete|remove|apply)\b", re.I)


def lean_type(ti):
    if ti.get("subagent_type") not in HEAVY:
        return None
    p = (ti.get("prompt") or "") + " " + (ti.get("description") or "")
    if NEEDS_FULL.search(p):
        return None
    return LEAN_BUILD if BUILDS.search(p) else LEAN_SEARCH


def main():
    if os.environ.get("COST_STACK_OFF"):
        return 0
    d = json.load(sys.stdin)
    if d.get("tool_name") not in ("Agent", "Task"):
        return 0
    ti = d.get("tool_input") or {}
    new = lean_type(ti)
    if not new:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "agent_lean: %s -> %s (smaller base prompt)" % (ti.get("subagent_type") or "default", new),
        "updatedInput": {**ti, "subagent_type": new, **({"model": ANALYSIS_MODEL} if new == LEAN_SEARCH and not ti.get("model")
            and ANALYSIS.search((ti.get("prompt") or "") + " " + (ti.get("description") or "")) else {})}}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
