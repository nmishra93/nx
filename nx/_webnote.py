"""Session-start web guidance: the note that steers the model toward web_fetch,
and its enable check.

Modeled on `_delegation_note.py` (same one-shot session-start user-role note +
marker-scan resume contract). Injected ONLY when the web_fetch schema is
actually offered (NX_WEBFETCH != 0) — see `_prefix.prefix_notes()`. The tool
description alone wasn't enough: models never reached for web_fetch unprompted
and answered current-versions/CVE/API-change questions straight out of training
data. A schema description reads as a *permission*, this reads as an
*expectation*, and it carries the trigger list. Kept out of the static SYSTEM
string so an offline session (NX_WEBFETCH=0) pays nothing for it."""
from __future__ import annotations

from ._env import _env_int

# Session-start web guidance, injected ONLY when the web_fetch schema is
# actually offered (NX_WEBFETCH != 0) — see _prefix.prefix_notes().
WEB_GUIDANCE = (
    "[Web note: the web_fetch tool is available this session — reach for it "
    "PROACTIVELY, without being asked, for anything where up-to-date "
    "information matters: current library versions, release notes/changelogs, "
    "recent CVEs, API changes, current best practices, an error message you "
    "don't recognize, or orienting in unfamiliar literature/docs/frameworks "
    "before coding. Even when confident, one quick web_fetch query for the "
    "latest docs is cheap insurance against stale training data. Local files "
    "first for project-local questions; web_fetch for the outside world.\n"
    "- With NX_SEARX_URL set (a self-hosted SearXNG) web search is markedly "
    "better — prefer it where available.]"
)

# The marker _add_web_note_if_absent scans for (mirrors DELEGATION_NOTE_MARKER).
WEB_NOTE_MARKER = "[Web note:"


def _web_enabled() -> bool:
    """True when the web_fetch schema is being offered this session.
    Resolved through the top-level module attribute (WEBFETCH_ENABLED) so
    tests/REPL can flip it live; falls back to the env var."""
    import nx as _m
    try:
        return bool(_m.WEBFETCH_ENABLED)
    except AttributeError:
        return bool(_env_int("NX_WEBFETCH", 1))


def _load_web_note() -> dict[str, str] | None:
    """User-role session-start note with the web triggers, or None when
    web_fetch is disabled this session (nothing to steer toward)."""
    if not _web_enabled():
        return None
    return {"role": "user", "content": WEB_GUIDANCE}


def _add_web_note_if_absent(messages: list) -> None:
    """Give a resumed session the same note a fresh one gets (its saved
    transcript may predate the feature). Mirrors
    _delegation_note._add_delegation_note_if_absent's marker-scan contract."""
    for m in messages:
        if (m.get("role") == "user" and isinstance(m.get("content"), str)
                and WEB_NOTE_MARKER in m["content"]):
            return
    note = _load_web_note()
    if note is not None:
        at = 1 if (messages and messages[0].get("role") == "system") else 0
        messages.insert(at, note)
