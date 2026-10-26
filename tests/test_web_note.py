"""0.171.0: the session-start [Web note:] steers models toward web_fetch
unprompted (models never reached for it; only the schema described it). Mirrors
the delegation-note contract: paid ONLY when the tool is offered
(WEBFETCH_ENABLED / NX_WEBFETCH != 0), marker-scanned on resume so a normal
resume stays byte-identical. No model/network round-trip.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("NX_ENV_FILE", "/dev/null")
for k in [k for k in os.environ if k.startswith("NX_")]:
    os.environ.pop(k, None)
os.environ["NX_ENV_FILE"] = "/dev/null"

import pytest  # noqa: E402


@pytest.fixture()
def nx_fresh(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("NX_")]:
        os.environ.pop(k, None)
    os.environ["NX_HOME"] = str(tmp_path / "nxhome")
    os.environ["NX_ENV_FILE"] = str(tmp_path / "env")
    os.environ["NX_SESSIONS_DIR"] = str(tmp_path / "nxhome" / "sessions")
    os.environ["NX_BASE_URL"] = "http://127.0.0.1:9/v1"
    os.environ["NX_MODEL"] = "test-model"
    os.environ["NX_API_KEY"] = "sk-test"
    monkeypatch.setattr(os, "environ", os.environ)
    monkeypatch.setattr(sys, "argv", ["nx"])
    import nx
    from nx._sources import _discover_sources, switch_source
    _discover_sources()
    assert switch_source(nx._state.SOURCE_ORDER[0]), "fixture source wiring failed"
    yield nx


def test_note_present_when_enabled(nx_fresh, monkeypatch):
    monkeypatch.setattr(nx_fresh, "WEBFETCH_ENABLED", 1, raising=False)
    msgs = nx_fresh._initial_messages()
    notes = [m for m in msgs if "[Web note:" in (m.get("content") or "")]
    assert len(notes) == 1, "fresh web-enabled session must carry the note"
    assert notes[0]["role"] == "user"
    # The triggers that motivate the note at all.
    body = notes[0]["content"]
    for needle in ("web_fetch", "PROACTIVELY", "CVE", "NX_SEARX_URL"):
        assert needle in body, (needle, body)


def test_note_absent_when_disabled(nx_fresh, monkeypatch):
    monkeypatch.setattr(nx_fresh, "WEBFETCH_ENABLED", 0, raising=False)
    msgs = nx_fresh._initial_messages()
    assert not [m for m in msgs if "[Web note:" in (m.get("content") or "")], \
        "NX_WEBFETCH=0 session must not pay for the note"
    import nx._webnote as w
    assert w._load_web_note() is None


def test_marker_contract_kept(nx_fresh, monkeypatch):
    monkeypatch.setattr(nx_fresh, "WEBFETCH_ENABLED", 1, raising=False)
    import nx._webnote as w
    assert w.WEB_NOTE_MARKER in w._load_web_note()["content"]


def test_note_not_duplicated_on_resume(nx_fresh, monkeypatch):
    monkeypatch.setattr(nx_fresh, "WEBFETCH_ENABLED", 1, raising=False)
    from nx._subagent import _add_web_note_if_absent
    msgs = [{"role": "system", "content": "sys"}]
    _add_web_note_if_absent(msgs)
    _add_web_note_if_absent(msgs)
    notes = [m for m in msgs if "[Web note:" in (m.get("content") or "")]
    assert len(notes) == 1, "note must be inserted at most once"
    # Inserted after the system message, not appended at the tail.
    assert msgs.index(notes[0]) == 1
    # Disabled: nothing added.
    monkeypatch.setattr(nx_fresh, "WEBFETCH_ENABLED", 0, raising=False)
    bare = [{"role": "system", "content": "sys"}]
    _add_web_note_if_absent(bare)
    assert len(bare) == 1


def test_prefix_notes_include_it_when_enabled(nx_fresh, monkeypatch):
    from nx import _prefix
    monkeypatch.setattr(nx_fresh, "WEBFETCH_ENABLED", 1, raising=False)
    notes = _prefix.prefix_notes()
    web = [n for n in notes if n["content"].startswith("[Web note:")]
    assert len(web) == 1, "prefix_notes() must carry the web note when enabled"
    # The marker scan the prefix pin relies on recognizes it.
    assert all(_prefix.is_prefix_note(n) for n in web)
    monkeypatch.setattr(nx_fresh, "WEBFETCH_ENABLED", 0, raising=False)
    assert not [n for n in _prefix.prefix_notes()
                if n["content"].startswith("[Web note:")]


def test_env_fallback_when_attr_missing(monkeypatch):
    # A fresh import surface without the live attribute still honors the env.
    import nx as _m
    import nx._webnote as w
    monkeypatch.delattr(_m, "WEBFETCH_ENABLED", raising=False)
    monkeypatch.setenv("NX_WEBFETCH", "0")
    assert w._load_web_note() is None
    monkeypatch.setenv("NX_WEBFETCH", "1")
    assert w._load_web_note() is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
