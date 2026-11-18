"""0.156.1 regression: a sub-agent sub-loop must never DELIVER the parent's
pending runtime notes. run_tool refuses to STAGE them inside a sub-agent loop
(the _dispatch.py first-tool-of-turn gate); before this fix the matching
FLUSH was ungated, so a sibling async agent's (or a sync agent's) tool-call
flush drained the parent's staged notes — including worker-staged module-store
notes — into the sub-loop's doomed messages list. The note's `announced` mark
was already burned at staging, so the parent never re-received it and
wake_due() saw nothing pending: the /tabs dashboard showed every agent done
while the main loop sat un-woken (the "all agents finished, never woken"
report).

The gate: _flush_pending_background_notice is a no-op while
_subagent_ctx() is set. Notes the sub-loop itself stages fold back into the
parent frame at pop (_notices), so nothing is lost either way."""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("NX_ENV_FILE", "/dev/null")

from nx import _notices as nz


class _Ctx:
    """Minimal stand-in for a _SubagentContext: presence is the whole test."""


def _in_subloop(fn):
    """Run fn with a sub-agent context installed on THIS thread (the flag
    _subagent_ctx() reads), restoring after."""
    from nx import _subagent_tls
    prev = getattr(_subagent_tls._SUBAGENT_TLS, "ctx", None)
    _subagent_tls._SUBAGENT_TLS.ctx = _Ctx()
    try:
        return fn()
    finally:
        _subagent_tls._SUBAGENT_TLS.ctx = prev


def test_subloop_flush_delivers_nothing():
    """Both channels — the frame slot and the module store — stay untouched
    for a sub-loop flush, and the parent's own flush still delivers them."""
    import nx as m
    st = m.st
    slots = m._runtime_notes._MODULE_NOTE_SLOTS
    saved = {s: getattr(st, s, None) for s in slots}
    nz._CURRENT.set(None)
    # A note staged BEFORE the loop (frame-seeded) and one staged into the
    # module store by a frameless worker mid-loop: the two shapes the theft
    # bug consumed.
    st._PENDING_SUBAGENT_NOTICE = "[Runtime note: agent A finished]"
    fr = nz.install_notice_ctx()
    try:
        nz.set_("_PENDING_VERIFY_NOTICE", "[Runtime note: verify passed]")

        def flush_in_subloop():
            msgs: list = []
            m._runtime_notes._flush_pending_background_notice(msgs)
            return msgs

        assert _in_subloop(flush_in_subloop) == [], \
            "a sub-agent sub-loop must not deliver the parent's notes"
        # Both channels intact for the parent.
        assert nz.get("_PENDING_SUBAGENT_NOTICE") == "[Runtime note: agent A finished]"
        assert nz.get("_PENDING_VERIFY_NOTICE") == "[Runtime note: verify passed]"
        # The parent's own flush still delivers everything, once.
        parent_msgs: list = []
        m._runtime_notes._flush_pending_background_notice(parent_msgs)
        assert set(mm["content"] for mm in parent_msgs) == {
            "[Runtime note: agent A finished]", "[Runtime note: verify passed]"}, \
            parent_msgs
    finally:
        nz._CURRENT.set(fr)
        nz.pop_notice_ctx(fr)
        for s, v in saved.items():
            setattr(st, s, v)


def test_parent_flush_untouched_outside_subloop():
    """Guard on the guard: the same call outside a sub-agent loop still
    delivers (this is the 0.144.x wake-misfire contract)."""
    import nx as m
    st = m.st
    saved = st._PENDING_SUBAGENT_NOTICE
    nz._CURRENT.set(None)
    st._PENDING_SUBAGENT_NOTICE = "[Runtime note: agent B finished]"
    try:
        msgs: list = []
        m._runtime_notes._flush_pending_background_notice(msgs)
        assert [mm["content"] for mm in msgs] == ["[Runtime note: agent B finished]"]
        assert st._PENDING_SUBAGENT_NOTICE is None
    finally:
        st._PENDING_SUBAGENT_NOTICE = saved


# ---------------------------------------------------------------------------
# 0.172 wake-loss fix: `announced` means DELIVERED, not staged. A note burned
# at staging could never be re-staged once lost (sibling drain / frame-vs-store
# mismatch / the sub-loop flush gate above), so wake_due() saw nothing pending
# and the parent sat un-woken forever. These three lock the recovery contract.
# ---------------------------------------------------------------------------
def _seed_record(jid="agent-lost-1", status="done", answer="the answer"):
    from nx._subagent import _ASYNC_AGENTS, _ASYNC_LOCK
    with _ASYNC_LOCK:
        _ASYNC_AGENTS[jid] = {
            "instruction": "q", "status": status, "answer": answer,
            "started": 0.0, "announced": False,
        }
    return jid


def test_staging_does_not_burn_announced():
    """Staging the note marks the record staged, NOT announced — until the note
    is actually delivered to a transcript."""
    import nx as m
    from nx._subagent import _stage_subagent_answer, _ASYNC_AGENTS, _ASYNC_LOCK
    nz._CURRENT.set(None)
    for s in m._runtime_notes._MODULE_NOTE_SLOTS:
        setattr(m.st, s, None)
    jid = _seed_record()
    try:
        _stage_subagent_answer(jid)
        with _ASYNC_LOCK:
            rec = _ASYNC_AGENTS[jid]
            assert rec.get("staged") is True
            assert rec.get("announced") is not True   # NOT burned at staging
            note = rec.get("_note")
        assert m.st._PENDING_SUBAGENT_NOTICE == note
    finally:
        with _ASYNC_LOCK:
            _ASYNC_AGENTS.pop(jid, None)
        m.st._PENDING_SUBAGENT_NOTICE = None


def test_lost_note_is_restageable_and_still_wakes():
    """A staged note drained out from under the parent (the bug) leaves the
    record wake-eligible, the sweep re-stages the EXACT cached note (no dup),
    and only the real delivery flips `announced`."""
    import nx as m
    from nx import _autowake
    from nx._subagent import (_stage_subagent_answer, _stage_subagent_completions,
                              _ASYNC_AGENTS, _ASYNC_LOCK)
    nz._CURRENT.set(None)
    for s in m._runtime_notes._MODULE_NOTE_SLOTS:
        setattr(m.st, s, None)
    m.st._WAKE_ARMED = True
    m.st._TURN_ACTIVE = False
    jid = _seed_record()
    try:
        _stage_subagent_answer(jid)
        with _ASYNC_LOCK:
            note = _ASYNC_AGENTS[jid]["_note"]
        # The loss: something drained the slot without delivering it (a sibling
        # turn loop, or the sub-loop flush gate discarding into a doomed list).
        m.st._PENDING_SUBAGENT_NOTICE = None
        # Still wake-worthy — this is exactly what the old bug destroyed.
        assert _autowake._finished_unstaged_subagents() == [jid]
        # Recovery: the sweep re-stages the exact cached text...
        _stage_subagent_completions()
        assert m.st._PENDING_SUBAGENT_NOTICE == note
        # ...and a re-sweep while it is pending is a no-op (no duplicate).
        _stage_subagent_completions()
        assert m.st._PENDING_SUBAGENT_NOTICE == note
        # Only delivery retires it.
        delivered: list = []
        m._runtime_notes._flush_pending_background_notice(delivered)
        assert note in [d["content"] for d in delivered]
        with _ASYNC_LOCK:
            assert _ASYNC_AGENTS[jid]["announced"] is True
        assert _autowake._finished_unstaged_subagents() == []
    finally:
        with _ASYNC_LOCK:
            _ASYNC_AGENTS.pop(jid, None)
        m.st._PENDING_SUBAGENT_NOTICE = None


def test_new_conversation_retires_undelivered_notes():
    """A note cleared WITHOUT delivery (the /new session reset) is retired, not
    re-staged into the fresh conversation — the one place a dropped note stays
    'consumed' (0.128.3 intent, preserved across the 0.172 semantics change)."""
    import nx as m
    from nx._subagent import (_stage_subagent_answer, _stage_subagent_completions,
                              _retire_undelivered_notes, _ASYNC_AGENTS, _ASYNC_LOCK)
    nz._CURRENT.set(None)
    for s in m._runtime_notes._MODULE_NOTE_SLOTS:
        setattr(m.st, s, None)
    jid = _seed_record("agent-newconv", answer="old session answer")
    try:
        _stage_subagent_answer(jid)
        assert m.st._PENDING_SUBAGENT_NOTICE  # staged, pending
        m.st._PENDING_SUBAGENT_NOTICE = None  # /new drops the slot
        _retire_undelivered_notes()
        _stage_subagent_completions()
        assert m.st._PENDING_SUBAGENT_NOTICE is None, \
            "the outgoing session's note must not be re-staged into the new one"
        with _ASYNC_LOCK:
            assert _ASYNC_AGENTS[jid]["announced"] is True
    finally:
        with _ASYNC_LOCK:
            _ASYNC_AGENTS.pop(jid, None)
        m.st._PENDING_SUBAGENT_NOTICE = None


def test_retire_is_scoped_to_one_owner():
    """A batch task tearing down retires ONLY its own staged notes — a sibling
    task's still-pending note must stay wake-eligible (0.163.0 Phase E
    ownership must survive the 0.172 retire path)."""
    import threading
    import nx as m
    from nx._subagent import (_stage_subagent_answer, _retire_undelivered_notes,
                              _ASYNC_AGENTS, _ASYNC_LOCK)
    nz._CURRENT.set(None)
    for s in m._runtime_notes._MODULE_NOTE_SLOTS:
        setattr(m.st, s, None)
    mine, sibling = threading.get_ident(), threading.get_ident() + 1
    for jid, ident in (("agent-mine", mine), ("agent-sibling", sibling)):
        _seed_record(jid, answer=f"answer for {jid}")
        with _ASYNC_LOCK:
            _ASYNC_AGENTS[jid]["owner_ident"] = ident
    try:
        _stage_subagent_answer("agent-mine")
        _stage_subagent_answer("agent-sibling")
        _retire_undelivered_notes(mine)
        with _ASYNC_LOCK:
            assert _ASYNC_AGENTS["agent-mine"]["announced"] is True
            assert _ASYNC_AGENTS["agent-sibling"]["announced"] is not True, \
                "retiring one owner burned a sibling's note"
    finally:
        with _ASYNC_LOCK:
            _ASYNC_AGENTS.pop("agent-mine", None)
            _ASYNC_AGENTS.pop("agent-sibling", None)
        m.st._PENDING_SUBAGENT_NOTICE = None


def test_delivery_attributed_by_note_text_not_sibling():
    """A merged note that only carries this record's text marks THIS record; a
    sibling whose note is still pending is left un-announced (never burned)."""
    import nx as m
    from nx._subagent import (_stage_subagent_answer, _ASYNC_AGENTS, _ASYNC_LOCK)
    nz._CURRENT.set(None)
    for s in m._runtime_notes._MODULE_NOTE_SLOTS:
        setattr(m.st, s, None)
    jid_a = _seed_record("agent-attr-a", answer="alpha-answer")
    jid_b = _seed_record("agent-attr-b", answer="beta-answer")
    try:
        _stage_subagent_answer(jid_a)
        _stage_subagent_answer(jid_b)  # merges into the same slot
        merged = m.st._PENDING_SUBAGENT_NOTICE
        assert "alpha-answer" in merged and "beta-answer" in merged
        # Deliver ONLY a slice carrying A's note (as if B's were staged into a
        # different, sibling-owned slot): only A is marked.
        from nx._subagent import _mark_notes_delivered
        with _ASYNC_LOCK:
            note_a = _ASYNC_AGENTS[jid_a]["_note"]
        _mark_notes_delivered(note_a)
        with _ASYNC_LOCK:
            assert _ASYNC_AGENTS[jid_a]["announced"] is True
            assert _ASYNC_AGENTS[jid_b]["announced"] is not True
    finally:
        with _ASYNC_LOCK:
            _ASYNC_AGENTS.pop(jid_a, None)
            _ASYNC_AGENTS.pop(jid_b, None)
        m.st._PENDING_SUBAGENT_NOTICE = None
