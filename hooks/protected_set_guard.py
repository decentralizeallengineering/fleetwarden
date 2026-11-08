#!/usr/bin/env python3
"""protected_set_guard.py: Claude Code PreToolUse hook for the Bash tool (matcher "Bash").

Purpose: a configurable protected set (backups, history, keys) that no session may delete or overwrite.
Config: see configure() below. Fire log: $SUDO_GUARD_LOG (default ~/.claude/protected-set-guard.log).

Input: hook JSON on stdin (tool_input.command, cwd).
Output: exit 2 with a one-line reason on stderr to BLOCK; exit 0 to allow.
Blocks only destructive operations (delete, move-over, truncate, overwrite,
redirect, disk wipe/repartition/format, zpool destroy) that touch a protected
path, or a disk/partition whose filesystem holds one. Standard library only.
"""
import fnmatch
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
DEFAULT_PROTECTED = ["~/.ssh", "~/.claude/projects", "~/.claude/.credentials.json"]
PROTECTED = []
BROAD = []
NAMES = []
APPEND_ONLY = []


def configure(protected=None, names=None, append_only=None):
    """Set the protected set. Sources, first hit wins: arguments, env, config file, defaults.
    SUDO_GUARD_PROTECTED: colon-separated paths (~ allowed). SUDO_GUARD_PROTECTED_FILE: one path per line
    (default ~/.config/fleetwarden/protected-paths). SUDO_GUARD_NAMES: comma-separated exact file names that
    are protected wherever they live. SUDO_GUARD_APPEND_ONLY: comma-separated directory names where appending
    (>>) stays allowed."""
    global PROTECTED, BROAD, NAMES, APPEND_ONLY
    if protected is None:
        env = os.environ.get("SUDO_GUARD_PROTECTED")
        if env:
            protected = [p for p in env.split(":") if p]
        else:
            f = os.environ.get("SUDO_GUARD_PROTECTED_FILE") or os.path.expanduser("~/.config/fleetwarden/protected-paths")
            try:
                protected = [l.strip() for l in open(f) if l.strip() and not l.startswith("#")]
            except OSError:
                protected = list(DEFAULT_PROTECTED)
    PROTECTED = [os.path.normpath(os.path.expanduser(p)) for p in protected]
    # substrings for the fail-closed and indirect-target heuristics: path relative to $HOME, else base name
    BROAD = []
    for p in PROTECTED:
        BROAD.append(p[len(HOME) + 1:] if p.startswith(HOME + "/") else os.path.basename(p))
    if names is None:
        names = [n for n in os.environ.get("SUDO_GUARD_NAMES", "").split(",") if n]
    NAMES = list(names)
    if append_only is None:
        append_only = [n for n in os.environ.get("SUDO_GUARD_APPEND_ONLY", "").split(",") if n]
    APPEND_ONLY = list(append_only)


configure()

LOG_PATH = os.environ.get("SUDO_GUARD_LOG") or os.path.expanduser("~/.claude/protected-set-guard.log")
LSBLK_CACHE = "/tmp/protected_set_guard_lsblk.json"
LSBLK_TTL = 60
LSBLK_OVERRIDE = None  # tests only

DISK_RE = re.compile(r"^/dev/(sd|hd|vd|xvd|nvme|mmcblk|md|dm-|loop|sr|zd|rbd|nbd|bcache|sg|mapper/|disk/)")
DELETE_VERBS = {"rm", "rmdir", "unlink", "shred"}
COPY_VERBS = {"cp", "install", "ln", "rsync"}
WRITE_VERBS = {"tee", "truncate", "sed"}
DISK_TOOLS = {"wipefs", "sgdisk", "gdisk", "sfdisk", "parted", "fdisk", "cfdisk",
              "blkdiscard", "mke2fs", "mkswap"}
XARGS_DANGER = DELETE_VERBS | COPY_VERBS | WRITE_VERBS | DISK_TOOLS | {"mv", "dd"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
INTERP = {"python", "python3", "perl", "node", "ruby", "awk", "gawk"}
CODE_DANGER = re.compile(r"rmtree|unlink|remove\(|rmdir|truncate|shutil\.(move|copy)|"
                         r"os\.(rename|replace|system)|system\(|subprocess|-delete|wipefs|"
                         r"mkfs|\brm\b|open\([^)]*['\"][wa]")
CTRL = {";", "&&", "||", "|", "|&", "&", ";;", "(", ")"}
REDIR_OPS = {">", ">>", ">|", "&>", "&>>", "<>"}
SUBST_RE = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
SSH_ARG_OPTS = set("bcDEeFIiJLlmOoPpQRSWw")
WRAP_ARG_OPTS = {"sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U"},
                 "doas": {"-u", "-C"}}


# ---------- path helpers ----------

def normalize(s):
    s = s.replace("${HOME}", HOME).replace("$HOME", HOME)
    return re.sub(r"(?<![\w.~])~(?=/|$|[\s;&|()'\"])", HOME, s)


def is_glob(s):
    return any(c in s for c in "*?[")


def ambiguous(tok):
    return "$" in tok or "SUBST" in tok


def broad_protected(s):
    return any(f in s for f in BROAD)


def realpath_safe(t):
    try:
        return os.path.realpath(t)
    except Exception:
        return t


def norm_abs(tok, cwd):
    if not tok or tok.startswith("&"):
        return None
    t = tok
    if t == "~" or t.startswith("~/"):
        t = HOME + t[1:]
    return os.path.normpath(os.path.join(cwd, t))


def name_hit(bn):
    if not bn:
        return False
    if is_glob(bn):
        # a bare "*" must not count; the glob needs a literal run of 3+ characters of the protected name
        lits = [p for p in re.split(r"[*?\[\]]", bn) if len(p) >= 3]
        return any(fnmatch.fnmatchcase(n, bn) and any(p in n for p in lits) for n in NAMES)
    return any(bn == n for n in NAMES)


def write_block(t):
    """True if writing onto t touches a protected path (t inside or equal to one)."""
    return any(t.startswith(p) for p in PROTECTED) or name_hit(os.path.basename(t))


def p_ancestor(t):
    """True if deleting t would remove a protected path (t equal to or above one)."""
    base = t.rstrip("/")
    return any(p == t or p.startswith(base + "/") for p in PROTECTED)


def del_block(t):
    if is_glob(t):
        idx = min(t.index(c) for c in "*?[" if c in t)
        lit = t[:idx]
        if any(p.startswith(lit) or lit.startswith(p) for p in PROTECTED):
            return True
        try:
            hits = glob.glob(t)[:20000]
        except Exception:
            hits = []
        if any(write_block(h) or p_ancestor(h) for h in hits):
            return True
        return name_hit(os.path.basename(t))
    for x in (t, realpath_safe(t)):
        if write_block(x) or p_ancestor(x):
            return True
    return False


def write_reason(tok, cwd, verb):
    t = norm_abs(tok, cwd)
    if t and (write_block(t) or write_block(realpath_safe(t))):
        return f"{verb} writes onto protected path {t}"
    return None


def is_disk(tok):
    return bool(DISK_RE.match(realpath_safe(tok)))


def dest_reason(tok, cwd, verb):
    if is_disk(tok):
        return disk_reason(realpath_safe(tok))
    return write_reason(tok, cwd, verb)


# ---------- disk helpers ----------

def lsblk_data():
    if LSBLK_OVERRIDE is not None:
        return LSBLK_OVERRIDE
    try:
        st = os.stat(LSBLK_CACHE)
        if st.st_uid == os.getuid() and time.time() - st.st_mtime < LSBLK_TTL:
            with open(LSBLK_CACHE) as f:
                return json.load(f)
    except Exception:
        pass
    out = subprocess.run(["lsblk", "-J", "-p", "-o", "NAME,MOUNTPOINT,TYPE"],
                         capture_output=True, text=True, timeout=5)
    if out.returncode != 0:
        raise RuntimeError("lsblk failed")
    data = json.loads(out.stdout)
    try:
        tmp = f"{LSBLK_CACHE}.{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, LSBLK_CACHE)
    except Exception:
        pass
    return data


def find_node(nodes, name):
    for n in nodes or []:
        if n.get("name") == name:
            return n
        r = find_node(n.get("children"), name)
        if r:
            return r
    return None


def node_mounts(node):
    out = []
    if node.get("mountpoint"):
        out.append(node["mountpoint"])
    for m in node.get("mountpoints") or []:
        if m:
            out.append(m)
    for c in node.get("children") or []:
        out.extend(node_mounts(c))
    return out


def fs_holds_protected(mp):
    if mp.startswith("["):
        return False
    base = mp.rstrip("/")
    return any(p == mp or p.startswith(base + "/") for p in PROTECTED)


def disk_reason(dev):
    try:
        data = lsblk_data()
    except Exception:
        return f"cannot query lsblk for {dev}; fail closed"
    node = find_node(data.get("blockdevices", []), dev)
    if node is None:
        return f"{dev} not in lsblk output; fail closed"
    for mp in node_mounts(node):
        if fs_holds_protected(mp):
            return f"{dev} (or a child partition) holds a protected filesystem mounted at {mp}"
    return None


def zpool_members(pool):
    try:
        out = subprocess.run(["zpool", "status", "-P", pool],
                             capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [t for line in out.stdout.splitlines() for t in line.split() if t.startswith("/")]


def readonly_disk(verb, args):
    if verb == "fdisk":
        return "-l" in args
    if verb == "sfdisk":
        return any(a in ("-l", "-d", "--list", "--dump") for a in args)
    if verb == "sgdisk":
        return (any(a in ("-p", "--print", "-i", "--info") for a in args)
                and not any(a in ("-Z", "--zap-all", "-o", "--clear") for a in args))
    if verb == "parted":
        return ("print" in args and
                not any(a in ("rm", "mkpart", "mklabel", "mkfs", "resizepart") for a in args))
    return False


# ---------- parsing helpers ----------

def lex(s):
    lx = shlex.shlex(s, posix=True, punctuation_chars=True)
    lx.whitespace_split = True
    lx.commenters = ""
    return list(lx)


def segments(toks):
    segs, cur = [], []
    for t in toks:
        if t in CTRL:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    return segs


def split_redirs(seg):
    argv, redirs, i = [], [], 0
    while i < len(seg):
        t = seg[i]
        if t in REDIR_OPS:
            # an append-only ledger directory (SUDO_GUARD_APPEND_ONLY): appending (>>) there is never destructive
            if i + 1 < len(seg) and not (t in (">>", "&>>") and any(re.search(r"(^|/)%s/" % re.escape(n), seg[i + 1]) for n in APPEND_ONLY)
                                         and ".." not in seg[i + 1]):
                redirs.append(seg[i + 1])
            i += 2
            continue
        argv.append(t)
        i += 1
    return argv, redirs


def extract_subst(s):
    inners = []
    while True:
        m = SUBST_RE.search(s)
        if not m:
            break
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        inners.append(inner)
        s = s[:m.start()] + " SUBST " + s[m.end():]
    return s, inners


def strip_wrappers(argv):
    i = 0
    while i < len(argv):
        w = argv[i]
        base = os.path.basename(w)
        if w in ("{", "}", "!", "then", "do", "else", "if", "while", "until") \
                or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w):
            i += 1
            continue
        if base in ("sudo", "doas"):
            i += 1
            opts = WRAP_ARG_OPTS[base]
            while i < len(argv) and argv[i].startswith("-"):
                i += 2 if argv[i] in opts else 1
            continue
        if base == "env":
            i += 1
            while i < len(argv) and (argv[i].startswith("-") or "=" in argv[i]):
                i += 2 if argv[i] in ("-u", "-C", "-S") else 1
            continue
        if base in ("nohup", "time", "exec", "command", "builtin", "setsid"):
            i += 1
            continue
        if base in ("nice", "ionice", "stdbuf"):
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 2 if argv[i] in ("-n", "-c", "-p", "-o", "-e", "-i") else 1
            continue
        if base == "timeout":
            i += 1
            while i < len(argv) and argv[i].startswith("-"):
                i += 2 if argv[i] in ("-s", "-k") else 1
            i += 1  # duration
            continue
        break
    return argv[i:]


def targets(args):
    out, end = [], False
    for a in args:
        if not end and a == "--":
            end = True
            continue
        if not end and a.startswith("-") and a != "-":
            continue
        out.append(a)
    return out


# ---------- decision ----------

def handle(verb, args, cwd, depth):
    """Return (reason or None, destructive_flag)."""
    ts = targets(args)

    if verb in DELETE_VERBS:
        amb = False
        for a in ts:
            if ambiguous(a):
                amb = True
                continue
            t = norm_abs(a, cwd)
            if t and del_block(t):
                return f"{verb} would remove protected path {t}", True
        return None, amb

    if verb == "mv":
        amb = False
        if len(ts) >= 2:
            for s in ts[:-1]:
                if ambiguous(s):
                    amb = True
                    continue
                t = norm_abs(s, cwd)
                if t and del_block(t):
                    return f"mv would move protected path {t}", True
            if ambiguous(ts[-1]):
                amb = True
            else:
                r = dest_reason(ts[-1], cwd, "mv")
                if r:
                    return r, True
        return None, amb

    if verb in COPY_VERBS:
        dest = None
        for i, a in enumerate(args):
            if a == "-t" and i + 1 < len(args):
                dest = args[i + 1]
            elif a.startswith("--target-directory="):
                dest = a.split("=", 1)[1]
        if dest is None and ts:
            dest = ts[-1]
        if dest is None:
            return None, False
        if ambiguous(dest):
            return None, True
        r = dest_reason(dest, cwd, verb)
        if r:
            return r, True
        if verb == "rsync" and any(a == "--delete" or a.startswith("--delete-") for a in args):
            t = norm_abs(dest, cwd)
            if t and p_ancestor(t):
                return f"rsync --delete into {t} would remove protected paths", True
        return None, False

    if verb == "dd":
        amb = False
        for a in args:
            if a.startswith("of="):
                v = a[3:]
                if ambiguous(v):
                    amb = True
                    continue
                r = dest_reason(v, cwd, "dd")
                if r:
                    return r, True
        return None, amb

    if verb in WRITE_VERBS:
        if verb == "sed" and not any(
                (a.startswith("-") and not a.startswith("--") and "i" in a[1:])
                or a.startswith("--in-place") for a in args):
            return None, False
        amb = False
        for a in ts:
            if ambiguous(a):
                amb = True
                continue
            r = dest_reason(a, cwd, verb)
            if r:
                return r, True
        return None, amb

    if verb.startswith("mkfs") or verb in DISK_TOOLS:
        if readonly_disk(verb, args):
            return None, False
        for a in args:
            if a.startswith("-") or ambiguous(a):
                continue
            if is_disk(a):
                r = disk_reason(realpath_safe(a))
            elif "/" in a or a.startswith("~"):
                r = write_reason(a, cwd, verb)
            else:
                r = None
            if r:
                return r, True
        return None, True

    if verb == "zpool":
        pos = [a for a in args if not a.startswith("-")]
        if len(pos) >= 2 and pos[0] == "destroy":
            members = zpool_members(pos[1])
            if members is None:
                return "zpool destroy: cannot enumerate pool members; fail closed", True
            for m in members:
                r = dest_reason(m, cwd, "zpool destroy")
                if r:
                    return r, True
            return None, True
        if pos and pos[0] == "labelclear":
            for d in pos[1:]:
                r = dest_reason(d, cwd, "zpool labelclear")
                if r:
                    return r, True
            return None, True
        return None, False

    if verb == "find":
        start = []
        for a in args:
            if a.startswith("-") or a in ("(", "!", ")"):
                break
            start.append(a)
        deletes = "-delete" in args
        for k, a in enumerate(args[:-1]):
            if a in ("-exec", "-execdir", "-ok") and \
                    os.path.basename(args[k + 1]) in ("rm", "rmdir", "shred", "unlink"):
                deletes = True
        if not deletes:
            return None, False
        for s in (start or ["."]):
            if ambiguous(s):
                return None, True
            t = norm_abs(s, cwd)
            if t and del_block(t):
                return f"find would delete under protected path {t}", True
        return None, True

    if verb == "git":
        gdir, i = cwd, 0
        while i < len(args):
            a = args[i]
            if a == "-C" and i + 1 < len(args):
                gdir = norm_abs(args[i + 1], gdir) or gdir
                i += 2
            elif a in ("-c", "--git-dir", "--work-tree", "--namespace"):
                i += 2
            elif a.startswith("-"):
                i += 1
            else:
                break
        sub = args[i] if i < len(args) else ""
        rest = args[i + 1:]
        if sub == "clean" or (sub == "reset" and "--hard" in rest):
            if write_block(gdir) or write_block(realpath_safe(gdir)):
                return f"git {sub} inside protected path {gdir}", True
        return None, False

    if verb == "xargs":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-I", "-n", "-P", "-d", "-L", "-s", "-a", "-E", "-e") else 1
        sub = os.path.basename(args[i]) if i < len(args) else ""
        return None, (sub in XARGS_DANGER or sub.startswith("mkfs"))

    if verb == "ssh":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            opt = args[i]
            i += 2 if (len(opt) == 2 and opt[1] in SSH_ARG_OPTS) else 1
        remote = args[i + 1:]  # args[i] is the host
        if remote:
            r = check(" ".join(remote), HOME, depth + 1)
            return r, False
        return None, False

    if verb in SHELLS:
        for k, a in enumerate(args):
            if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", a) and k + 1 < len(args):
                return check(args[k + 1], cwd, depth + 1), False
        return None, False

    if verb == "eval" and args:
        return check(" ".join(args), cwd, depth + 1), False

    if verb in INTERP:
        joined = " ".join(args)
        if broad_protected(joined) and CODE_DANGER.search(joined):
            return f"{verb} script references a protected path with a destructive call", True
        return None, False

    return None, False


def check(cmd, cwd, depth=0):
    if depth > 6:
        return None
    full = normalize(cmd)
    body, inners = extract_subst(full)
    for inner in inners:
        r = check(inner, cwd, depth + 1)
        if r:
            return r
    toks = lex(body.replace("\n", " ; "))
    loopish = (bool(inners) or "$" in body or "xargs" in toks
               or re.search(r"\b(for|while|until)\b", body) is not None)
    destructive = False
    for seg in segments(toks):
        argv, redirs = split_redirs(seg)
        for tgt in redirs:
            if ambiguous(tgt):
                destructive = True
                continue
            r = dest_reason(tgt, cwd, "redirect")
            if r:
                return r
        argv = strip_wrappers(argv)
        if not argv:
            continue
        verb = os.path.basename(argv[0])
        args = argv[1:]
        if verb == "cd":
            tgt = next((a for a in args if not a.startswith("-")), HOME)
            cwd = norm_abs(tgt, cwd) or cwd
            continue
        reason, d = handle(verb, args, cwd, depth)
        if reason:
            return reason
        destructive = destructive or d
    if destructive and loopish and broad_protected(full):
        return "destructive command with indirect target referencing a protected path"
    return None


def decide(cmd, cwd):
    """Return a block reason, or None to allow. Fails closed only on parse error
    when the command contains a protected path string."""
    try:
        return check(cmd, cwd)
    except Exception as e:
        if broad_protected(normalize(cmd)):
            return f"unparseable command referencing a protected path ({type(e).__name__}); fail closed"
        return None


def log_block(cmd, reason):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        one = cmd[:200].replace("\r", "\\r").replace("\n", "\\n")
        with open(LOG_PATH, "a") as f:
            f.write(f"{ts} | {one} | {reason}\n")
    except Exception:
        pass


def main():
    try:
        data = json.loads(sys.stdin.read())
        cmd = (data.get("tool_input") or {}).get("command")
        cwd = data.get("cwd") or os.getcwd()
    except Exception:
        return 0
    if not isinstance(cmd, str) or not cmd.strip():
        return 0
    reason = decide(cmd, cwd)
    if reason:
        log_block(cmd, reason)
        sys.stderr.write(f"protected-set-guard: BLOCKED: {reason}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
