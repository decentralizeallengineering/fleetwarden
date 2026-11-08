# fleetwarden

[![tests](https://github.com/decentralizeallengineering/fleetwarden/actions/workflows/tests.yml/badge.svg)](https://github.com/decentralizeallengineering/fleetwarden/actions/workflows/tests.yml) ![license](https://img.shields.io/badge/license-Apache--2.0-blue)

Governance hooks and a session launcher for Claude Code. The hooks keep sessions from stalling on approval prompts,
hanging in unbounded wait loops, polling on tight schedules, writing to a protected directory or spawning expensive
subagents, and they re-inject standing rules after every compaction. Python 3 standard library only.

## What is in it

| File | Hook event | Effect |
| --- | --- | --- |
| `hooks/rm_guard.py` | PreToolUse Bash | Denies `rm`/`rmdir` forms that trigger the built-in approval prompt (globs, unguarded `$var`, relative paths, `rm` inside `sh -c`, `xargs rm`, `find -exec rm`). Literal absolute paths and `"${VAR:?}"` pass. |
| `hooks/wait_guard.py` | PreToolUse Bash | Blocks unbounded `until/while ... sleep` loops and `pgrep -f` patterns that match their own command line. |
| `hooks/protected_dir_guard.py` | PreToolUse Bash, Write, Edit | Makes one directory read-only except an allow list. |
| `hooks/protected_set_guard.py` | PreToolUse Bash | Blocks destructive commands (rm, mv-over, truncate, redirects, dd, mkfs, wipefs, zpool destroy, xargs/find/ssh/`sh -c` forms) that touch a configurable protected set, or a disk whose filesystem holds one. Complements `protected_dir_guard.py`, which protects a single tree from Write/Edit. |
| `hooks/rm_catastrophe_guard.py` | PreToolUse Bash | Narrow companion to `rm_guard.py`: blocks only recursive `rm` on `/`, `$HOME`, `~`, unguarded `$VAR`, or paths outside `$HOME` and the temp dirs. |
| `hooks/hang_guard.py` | PreToolUse Bash | Blocks `sleep` over 300 s, `tail -f`, `watch`, builtin `wait`, editors and pagers unless `timeout N` or background. Complements `wait_guard.py` (loops and self-matching `pgrep`). |
| `hooks/kill_guard.py` | PreToolUse Bash | Blocks `kill`/`pkill`/`killall` that resolve to pid 1, claude processes, the tmux server, sshd, systemd or unnamed qemu VMs. |
| `hooks/haiku_cap.py` | PreToolUse (all tools) | Denies further tool calls from a Haiku subagent once its prompt passes `HAIKU_CAP` tokens (default 85000) and orders it to answer. |
| `hooks/rtk_widen.py` | PreToolUse Bash | Rewrites Bash commands through the `rtk` output filter when `rtk` is installed; no-op otherwise. |
| `hooks/hooklib.py` | helper | Fire log (`$HOOK_FIRES_LOG`, default `~/.claude/hook-fires.log`) and command-text cleaning shared by the hooks above. |
| `hooks/commit-msg-strip-trailers` | git commit-msg | Strips `Co-Authored-By:`, `Claude-Session:` and "Generated with" lines from commit messages. Install: `cp hooks/commit-msg-strip-trailers .git/hooks/commit-msg`. |
| `hooks/agent_lean.py` | PreToolUse Agent | Routes default subagent calls to small-prompt agents (`examples/agents`). |
| `hooks/cadence_guard.py` | PreToolUse ScheduleWakeup, CronCreate | Raises wakeup delays to a floor of 900 s and refuses sub-hourly crons. |
| `hooks/intent_inject.py` | SessionStart | Injects an intent summary plus the tail of a rules file, deduplicated per session. |
| `hooks/_role.py` | helper | Session role from `$CLAUDE_ROLE`, else the transcript's project folder. |
| `bin/claude-session-up` | | Starts one long-lived session in a tmux socket from an env file. |
| `systemd/claude@.service` | | User unit template that runs the launcher. |

## Install

    mkdir -p ~/.claude/hooks ~/.local/bin ~/.config/systemd/user ~/.config/claude-sessions ~/.claude/agents
    cp hooks/*.py ~/.claude/hooks/
    cp bin/claude-session-up ~/.local/bin/
    cp systemd/claude@.service ~/.config/systemd/user/
    cp examples/agents/*.md ~/.claude/agents/

Merge the `hooks` object from `examples/settings.hooks.json` into `~/.claude/settings.json`.

## Configure

All settings are environment variables with defaults; set them in the hook command or the session env file.

- `PROTECTED_DIR` (absolute path; unset disables the guard), `PROTECTED_DIR_ALLOW` (default `PROPOSALS.md,drafts/`), `PROTECTED_DIR_ADMIN` (roles allowed to edit).
- `INTENT_FILE` (default `~/.config/fleetwarden/intent.md`), `RULES_FILE` or `RULES_NAME` (default `RULES.md`, searched from the session cwd upward), `RULES_CAP` (default 3000 bytes), `HOOK_STATE_DIR`.
- `CADENCE_NO_WAKEUP_ROLES`, `CADENCE_OVERRIDE_FILE` (JSON `{"role": seconds}`), `TICK_POLICY` (optional executable printing `<seconds> <reason>`).
- `AGENT_LEAN_BUILD`, `AGENT_LEAN_SEARCH`, `AGENT_LEAN_ANALYSIS_MODEL`.
- `SUDO_GUARD_PROTECTED` (colon-separated paths) or `~/.config/fleetwarden/protected-paths` (one per line; defaults to `~/.ssh`, `~/.claude/projects`, `~/.claude/.credentials.json`), `SUDO_GUARD_NAMES` (file names protected anywhere), `SUDO_GUARD_APPEND_ONLY` (directory names where `>>` stays allowed), `SUDO_GUARD_LOG`; `RM_GUARD_ALLOWED` (extra roots for `rm_catastrophe_guard`); `HAIKU_CAP`; `HOOK_FIRES_LOG`.
- `COST_STACK_OFF=1` disables `agent_lean` and `cadence_guard`.
- Sessions: copy `examples/session.env` to `~/.config/claude-sessions/NAME.env`, then `systemctl --user enable --now claude@NAME`.

All hooks fail open: an internal error never blocks a tool call.

## Example

    echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf build/*"}}' | python3 hooks/rm_guard.py

prints a `permissionDecision: deny` JSON with the rewrite advice. The newer guards block with exit code 2 and one stderr line:

    echo '{"tool_name":"Bash","tool_input":{"command":"rm -rf ~"},"cwd":"/tmp"}' | python3 hooks/rm_catastrophe_guard.py; echo $?

prints the reason and `2`. Run the tests with `python3 -m pytest`.

## See also

See also: intentpin, leanagents

## License

Apache-2.0, see `LICENSE` and `NOTICE`.
