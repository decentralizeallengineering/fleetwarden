"""hooklib.py: shared helpers for the guard hooks (fire log, stdin, command text cleaning).

Purpose: one place for the hook-fires.log line, reading hook JSON, and stripping quoted text.
Why: every guard leaves one audit line, so a block can be explained afterwards.
Fire log: ~/.claude/hook-fires.log, one line per fire: utc<TAB>hook<TAB>action.
Removal condition: remove when no hook in this directory imports it.
"""
import json
import os
import re
import sys
import time

LOG = os.environ.get("HOOK_FIRES_LOG") or os.path.expanduser("~/.claude/hook-fires.log")


def fire(hook, action):
    try:
        action = re.sub(r"\s+", " ", str(action))[:300]
        with open(LOG, "a") as f:
            f.write("%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), hook, action))
    except Exception:
        pass


def read_input():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)


def strip_heredocs(cmd):
    return HEREDOC.sub("", cmd)


def strip_quotes(cmd):
    """Blank the inside of '...' and "..." so words in strings are not seen as commands."""
    c = strip_heredocs(cmd)
    c = re.sub(r"'[^']*'", "''", c)
    c = re.sub(r'"(?:\\.|[^"\\])*"', '""', c)
    return c


# a command word position: start, after ; & | newline ( { ` $( , optionally behind wrappers / VAR=x
POS = (r"(?:^|[;&|\n({`]|\$\()\s*(?:(?:sudo(?:\s+-\S+)*|nohup|time|exec|command|builtin|env|setsid|nice(?:\s+-n\s*\d+)?|rtk|"
       r"[A-Za-z_]\w*=\S*)\s+)*")


def deny(hook, why):
    fire(hook, "block: " + why)
    sys.stderr.write("%s: %s\n" % (hook, why))
    sys.exit(2)
