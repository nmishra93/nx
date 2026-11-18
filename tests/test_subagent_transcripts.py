"""0.173.0: sub-agent transcript persistence + no-ack wake wording.

1. _write_agent_transcript: every async agent's full message list lands in
   SESSION_HOME/agents/<slug>/<job_id>.messages.json after its sub-loop
   ends — the forensic record of what the agent actually saw (the
   "injected Hello instruction" complaint was unverifiable without it).
2. WAKE_INSTRUCTION / STALL_WAKE_INSTRUCTION / CONTINUATION_NUDGE no longer
   ask for a one-word/one-line acknowledgment: they ask the model to just
   end the turn (or say (done) and stop). The old shapes invited small
   models to paraphrase a phantom short-ack instruction and then police it
   as an "injection".
"""
import json
import os
import time

import pytest

import nx  # noqa: E402
import nx._subagent as S  # noqa: E402


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
    monkeypatch.setattr(__import__("sys"), "argv", ["nx"])
    import importlib
    importlib.reload(nx)
    yield nx
    with S._ASYNC_LOCK:
        S._ASYNC_AGENTS.clear()
    for k in ("NX_HOME", "NX_SESSIONS_DIR", "NX_BASE_URL", "NX_MODEL",
              "NX_API_KEY"):
        os.environ.pop(k, None)


# --- 1. transcript persistence ----------------------------------------------


def _seed_job(job_id, tier="read"):
    S._ASYNC_AGENTS[job_id] = {"status": "running", "answer": None,
                               "instruction": "find X", "announced": False,
                               "max_turns": 100, "tier": tier, "turns": 0}


def test_async_agent_transcript_persisted(nx_fresh, monkeypatch):
    """A completed async agent's messages land on disk as valid JSON."""
    monkeypatch.setattr(S, "_run_sync_agent_body",
                        lambda instruction, max_turns, tier="read", scope=None,
                        worktree=None, job_id=None, **_: "findings: X in nx/_x.py")
    monkeypatch.setattr(S, "_write_agent_report", lambda *a, **k: "/tmp/r.md")
    _seed_job("job-t1")
    S._run_async_agent("job-t1", "find X", 100, tier="read")
    path = os.path.join(S._agents_report_dir(), "job-t1.messages.json")
    assert os.path.exists(path), path
    with open(path, encoding="utf-8") as f:
        msgs = json.load(f)
    assert isinstance(msgs, list)


def test_transcript_write_failure_never_breaks_spawn(nx_fresh, monkeypatch):
    """A disk failure in the forensic write is silent — the agent's answer
    still finalizes and stages."""
    def boom(job_id, messages):
        raise OSError("read-only home")
    monkeypatch.setattr(S, "_write_agent_transcript", boom)
    monkeypatch.setattr(S, "_run_sync_agent_body",
                        lambda instruction, max_turns, tier="read", scope=None,
                        worktree=None, job_id=None, **_: "findings: Y")
    monkeypatch.setattr(S, "_write_agent_report", lambda *a, **k: "/tmp/r.md")
    _seed_job("job-t2")
    S._run_async_agent("job-t2", "find Y", 100, tier="read")
    assert S._ASYNC_AGENTS["job-t2"]["status"] == "done"


def test_killed_agent_transcript_also_persisted(nx_fresh, monkeypatch):
    """The write sits in _run_sync_agent_body's post-finally path, so a
    cooperative kill still leaves the forensic record (verify the write is
    reachable via the direct helper, since raising _SubagentKilled through
    the real loop needs the full turn machinery)."""
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "half-done"}]
    path = S._write_agent_transcript("job-t3", msgs)
    assert path and os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        assert json.load(f) == msgs
    # and it is private (0600)
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_purge_removes_old_transcripts(nx_fresh, monkeypatch):
    path = S._write_agent_transcript("job-t4", [{"role": "user", "content": "x"}])
    assert path and os.path.exists(path)
    # age the file past the TTL
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))
    S._purge_old_agent_reports(max_age_days=7)
    assert not os.path.exists(path)


# --- 2. ack-free wake/continuation wording -----------------------------------


def test_wake_instruction_asks_for_no_reply():
    import nx._autowake as _autowake
    assert "no reply" in _autowake.WAKE_INSTRUCTION
    assert "one line" not in _autowake.WAKE_INSTRUCTION


def test_stall_instruction_asks_for_no_reply():
    import nx._autowake as _autowake
    assert "no reply" in _autowake.STALL_WAKE_INSTRUCTION
    assert "one line" not in _autowake.STALL_WAKE_INSTRUCTION


def test_continuation_nudge_bounds_the_ack():
    from nx import _turn_toolcalls as TC
    # the old shape ("reply with the single word X and nothing else") is what
    # a small model paraphrased into a phantom "reply with only Hello"
    assert "single word" not in TC.CONTINUATION_NUDGE
    assert "(done)" in TC.CONTINUATION_NUDGE
    assert "do not invent any other acknowledgment" in TC.CONTINUATION_NUDGE
