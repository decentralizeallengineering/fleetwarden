"""Session role for hooks: $CLAUDE_ROLE, else the session's project dir (transcript_path slug, then cwd).
Never terminal state: an inherited $TMUX would make a test run from inside a named tmux look like that session."""
import os

HOME_SLUG = os.path.expanduser("~").replace("/", "-") + "-"


def role(d=None):
    r = os.environ.get("CLAUDE_ROLE")
    if r:
        return r
    d = d if isinstance(d, dict) else {}
    parts = (d.get("transcript_path") or "").split("/")
    if "projects" in parts and parts.index("projects") + 1 < len(parts):
        slug = parts[parts.index("projects") + 1]
        return slug[len(HOME_SLUG):] if slug.startswith(HOME_SLUG) else slug.rsplit("-", 1)[-1]
    return os.path.basename((d.get("cwd") or "").rstrip("/"))
