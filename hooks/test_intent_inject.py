import io, json, os, sys
H = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, H)
import intent_inject


def run(tmp_path, text, monkeypatch, intent=None):
    home = tmp_path; (home / "proj").mkdir()
    (home / "proj" / "RULES.md").write_text(text)
    (home / "proj" / "BRIEF.md").write_text("BRIEFMARK")
    monkeypatch.setattr(intent_inject, "HOME", str(home))
    monkeypatch.setattr(intent_inject, "STATE_DIR", str(home / "state"))
    monkeypatch.delenv("RULES_FILE", raising=False)
    if intent:
        (home / "intent.md").write_text(intent)
        monkeypatch.setenv("INTENT_FILE", str(home / "intent.md"))
    else:
        monkeypatch.setenv("INTENT_FILE", str(home / "none.md"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(home / "proj")})))
    out = io.StringIO(); monkeypatch.setattr(sys, "stdout", out)
    intent_inject.main()
    return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


def test_big_file_capped_newest_kept_pointer_present_no_brief(tmp_path, monkeypatch):
    text = "".join("- 2026-10-%02d entry %04d %s\n" % (i % 28 + 1, i, "x" * 200) for i in range(2000))
    ctx = run(tmp_path, text, monkeypatch)
    assert "entry 1999" in ctx and "entry 0000" not in ctx
    assert len(ctx.encode()) <= intent_inject.CAP + 1000
    assert "read the full file" in ctx and "RULES.md" in ctx
    assert "BRIEFMARK" not in ctx


def test_small_file_whole(tmp_path, monkeypatch):
    ctx = run(tmp_path, "- one\n- two\n", monkeypatch)
    assert "- one" in ctx and "- two" in ctx and "read the full file" not in ctx


def test_intent_file_rides_first(tmp_path, monkeypatch):
    ctx = run(tmp_path, "- one\n", monkeypatch, intent="INTENTMARK keep it small")
    assert "INTENTMARK" in ctx and ctx.index("INTENTMARK") < ctx.index("- one")
