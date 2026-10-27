import json, subprocess, os, pytest
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rm_guard.py")

def run(cmd, tool="Bash"):
    p = subprocess.run(["python3", H], input=json.dumps({"tool_name": tool, "tool_input": {"command": cmd}}),
                       capture_output=True, text=True)
    assert p.returncode == 0
    return p.stdout

DENY = [
    "cd /x; rm -f tpl/*", "rm -rf $DIR", 'rm -rf "$DIR/sub"', 'bash -c "rm -rf /tmp/x"',
    "rm -f *.log", "find . -name x | xargs rm", "rm -rf build", "find /a -name x -exec rm {} +",
    "rm -f `ls`", "rm -f $(ls /a)", "eval 'rm -f /a'", "sudo rm -rf tpl", "echo hi && rmdir foo",
    "for f in a b; do rm $f; done", "rm -f /a/x?.txt", "rm -rf ${D}/sub",
]
ALLOW = [
    "rm -f /tmp/x.txt", 'rm -rf "${D:?}/sub"', 'rm -rf "${D:?}"/sub', "git rm -r foo", "docker run --rm img",
    "python3 - <<'EOF'\nimport os; os.remove('/a')\nrm -rf *\nEOF", "ls; echo rm", "grep -rn rm file",
    "npm rm foo", "find /a -name x -delete", "rm -f ~/x.txt", "rm -rf -- /a/b", "rm -f '/a/*.log'",
    "ls 2>&1 | head", "python3 -c \"import os; os.remove('/a')\"", "echo 'rm -rf *'", "rm -f /a/x 2>/dev/null",
    "rm -f", "nice -n 10 rm -f /a/b",
]

@pytest.mark.parametrize("c", DENY)
def test_deny(c):
    o = json.loads(run(c))
    assert o["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert o["hookSpecificOutput"]["permissionDecisionReason"].startswith("rm_guard:")

@pytest.mark.parametrize("c", ALLOW)
def test_allow(c):
    assert run(c) == ""

def test_non_bash_and_garbage():
    assert run("rm -rf *", tool="Write") == ""
    p = subprocess.run(["python3", H], input="not json", capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout == ""
