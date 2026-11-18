---
title: "Wake reliability: main model misses subagent completion/failure"
status: active
touched: 2026-11-18
in_flight:
  - Fix 1: non-TTY fallback editor discards wake (nx/_chatbox.py:1203-1205)
  - Fix 2: announced burned at staging; lost note kills wake permanently (nx/_subagent.py:964, nx/_autowake.py:457-465)
  - Fix 3: streak-cap park never wake-eligible (nx/_autowake.py:579-598, _NON_WAKE_NOTICE_SLOTS:413-418)
next:
  - Merge agent worktrees, run full suite, lint, commit per fix
  - Add regression tests per fix
read_first:
  - docs/HANDOFF_wake_reliability.md
---

Full investigation report: /home/nmishra/.nx/agents/nx-64254506/agent-1-c2cfb7.md

Users across 100s of sessions report the main model not following up on background
subagent completion/failure. Root causes ranked:

1. Non-TTY sessions never wake: `read_multiline` fallback (`nx/_chatbox.py:1203-1205`)
   returns blocking `input()`, discarding `wake`.
2. `announced=True` set when the note is *staged* (`nx/_subagent.py:964`), not delivered.
   If the staged note is lost (global-store drain, frame mismatch, flush gate at
   `nx/_runtime_notes.py:79-81`), `_finished_unstaged_subagents()`
   (`nx/_autowake.py:457-465`) never fires again.
3. Streak cap (`nx/_autowake.py:579-598`) parks with a hold notice in
   `_NON_WAKE_NOTICE_SLOTS` that can never trigger a wake; chains look dead.
4. REPL re-check drops stale sentinels silently (`nx/_repl.py:482-483`).
5. Process death before delivery only recovered next session
   (`nx/_subagent.py:371+` orphan-intent sweep).

Fix scope agreed: 1, 2, 3 (4 and 5 are lower value / by design for now).
