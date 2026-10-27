import json, os, subprocess, sys
H = os.path.dirname(os.path.abspath(__file__))

def run(ti, tool="Agent", **env):
    e = {k: v for k, v in os.environ.items() if k != "COST_STACK_OFF"}
    e.update(env)
    r = subprocess.run([sys.executable, H + "/agent_lean.py"], input=json.dumps({"tool_name": tool, "tool_input": ti}), capture_output=True, text=True, env=e)
    return json.loads(r.stdout)["hookSpecificOutput"]["updatedInput"]["subagent_type"] if r.stdout.strip() else None

def test_general_purpose_read_becomes_search():
    # Rule: owner "CHEAP CHEAP SUBAGENTS"; lean-search for reading, lean-build for edits.
    assert run({"subagent_type": "general-purpose", "prompt": "Find where tickets.py opens sqlite; report file:line."}) == "lean-search"

def test_chore_and_default_edit_become_build():
    assert run({"subagent_type": "chore", "prompt": "Fix the typo in tools/gate.sh and run its test."}) == "lean-build"
    assert run({"prompt": "Implement the archive flag in tools/release_robot.py."}) == "lean-build"

def test_web_mcp_skill_kept():
    for p in ("Search the web for llama.cpp ctx flags.", "Read https://example.invalid/doc", "Use mcp__slack to read unreads.", "Run the graphify skill."):
        assert run({"subagent_type": "general-purpose", "prompt": p}) is None
    assert run({"subagent_type": "general-purpose", "prompt": "Fix web/static/shell.js icon."}) == "lean-build"  # repo dir, not the web

def test_other_types_and_tools_untouched():
    assert run({"subagent_type": "Explore", "prompt": "find x"}) is None
    assert run({"subagent_type": "lean-build", "prompt": "fix x"}) is None
    assert run({"command": "ls"}, tool="Bash") is None

def test_kill_switch_and_fields_kept():
    assert run({"subagent_type": "chore", "prompt": "fix x"}, COST_STACK_OFF="1") is None
    r = subprocess.run([sys.executable, H + "/agent_lean.py"], input=json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "chore", "prompt": "fix x", "run_in_background": True, "description": "d"}}), capture_output=True, text=True)
    u = json.loads(r.stdout)["hookSpecificOutput"]["updatedInput"]
    assert u["run_in_background"] is True and u["description"] == "d" and u["prompt"] == "fix x"

def test_bad_json_failopen():
    r = subprocess.run([sys.executable, H + "/agent_lean.py"], input="x", capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == ""
