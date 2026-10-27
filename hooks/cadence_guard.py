"""PreToolUse ScheduleWakeup|CronCreate hook: stop sessions from burning usage with frequent self-wakeups.

ScheduleWakeup: delaySeconds is raised to the policy interval (never below 900 s). Roles listed in
$CADENCE_NO_WAKEUP_ROLES (comma separated) get no self-wakeups at all.
CronCreate: a schedule that fires more often than hourly is refused, because a "* * * * *" pulse cron costs
1440 turns a day.
A per-role override file ($CADENCE_OVERRIDE_FILE, JSON {"role": seconds}, default
~/.config/fleetwarden/cadence-override.json) lets the operator set a shorter cadence for one role (floor 300 s).
$TICK_POLICY names an optional executable that prints "<seconds> <reason>" (default: tick-policy on PATH, else
~/.local/bin/tick-policy); when none is found the floor of 900 s applies.
Fail-open. Kill switch: COST_STACK_OFF=1."""
import json, os, re, shutil, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _role import role as session_role
FLOOR = 900


def no_wakeup_roles():
    return {r.strip() for r in os.environ.get("CADENCE_NO_WAKEUP_ROLES", "").split(",") if r.strip()}


def policy():
    try:
        exe = os.environ.get("TICK_POLICY") or shutil.which("tick-policy") or os.path.expanduser("~/.local/bin/tick-policy")
        out = subprocess.run([exe], capture_output=True, text=True, timeout=40).stdout.split()
        return max(FLOOR, int(out[0])), " ".join(out[1:])
    except Exception:
        return FLOOR, "no tick-policy, using floor"


def role_override(role):
    """Seconds the operator set for this role in the override file; 0 when none. Floor 300 s."""
    path = os.environ.get("CADENCE_OVERRIDE_FILE") or os.path.expanduser("~/.config/fleetwarden/cadence-override.json")
    try:
        v = json.load(open(path)).get(role or "", 0)
        return max(300, int(v)) if v else 0
    except Exception:
        return 0


def cron_ok(minute, secs):
    """True when the minute field ("*/20" or "7,27,47") never fires sooner than secs apart; False when secs is 0."""
    if not secs:
        return False
    m = re.fullmatch(r"\*/(\d+)", minute)
    mins = list(range(0, 60, int(m.group(1)))) if m else sorted(int(x) for x in minute.split(",") if x.isdigit())
    if not mins or (not m and len(mins) != len(minute.split(","))):
        return False
    gaps = [b - a for a, b in zip(mins, mins[1:])] + [mins[0] + 60 - mins[-1]]
    return min(gaps) * 60 >= secs


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


def main():
    if os.environ.get("COST_STACK_OFF"):
        return 0
    d = json.load(sys.stdin)
    name, ti = d.get("tool_name"), d.get("tool_input") or {}
    if name == "ScheduleWakeup":
        if ti.get("stop"):
            return 0
        override = role_override(session_role(d))
        if override:
            ti["delaySeconds"] = override
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": ti}}))
            return 0
        if session_role(d) in no_wakeup_roles():
            deny("cadence_guard: this role takes no self-wakeups; wait for the operator or a task notification.")
            return 0
        secs, why = policy()
        if int(ti.get("delaySeconds") or 0) < secs:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                "permissionDecisionReason": "cadence_guard: delay raised to %d s (%s)" % (secs, why),
                "updatedInput": {**ti, "delaySeconds": secs}}}))
        return 0
    if name == "CronCreate":
        minute = (ti.get("cron") or ti.get("schedule") or "0 * * * *").split()[0]
        # An operator-set cadence also admits a cron whose minutes are at least that far apart.
        if not re.fullmatch(r"\d{1,2}", minute) and not cron_ok(minute, role_override(session_role(d))):
            deny("cadence_guard: cron '%s' fires more often than hourly; use ScheduleWakeup (the policy floor) or a Monitor on the event." % (ti.get("cron") or ti.get("schedule")))
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
