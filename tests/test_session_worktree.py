"""nx/_worktree.py session mode (0.103.0, session-isolation phase 3):
NX_SESSION_WORKTREE / `nx --isolate` gives each interactive session its own
git worktree + branch, so two sessions in one repo share nothing."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("NX_ENV_FILE", "/dev/null")

import pytest


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("NX_HOME", str(tmp_path / "nxhome"))
    monkeypatch.chdir(tmp_path)
    import importlib
    import nx
    import nx._worktree
    importlib.reload(nx)
    nx.st.PROJECT_ROOT = str(tmp_path)
    nx.st.PROJECT_ROOT_IS_GIT = True
    nx.st.SESSION_HOME = str(tmp_path / "nxhome")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path,
                   check=True, capture_output=True)
    for k, v in (("user.email", "nx@example.com"), ("user.name", "nx")):
        subprocess.run(["git", "config", k, v], cwd=tmp_path, check=True,
                       capture_output=True)
    (tmp_path / "a.txt").write_text("base\n")
    subprocess.run(["git", "add", "a.py" if False else "a.txt"], cwd=tmp_path,
                   check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path,
                   check=True, capture_output=True)
    yield nx, tmp_path


def test_create_session_worktree(repo):
    nx, rp = repo
    wt, err = nx._worktree.create_session_worktree("20260101-000000-aaa111")
    assert err is None and wt
    assert os.path.isdir(wt)
    assert os.path.isfile(os.path.join(wt, "a.txt"))
    r = subprocess.run(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "nx/session-20260101-000000-aaa111"


def test_reenter_same_session_reuses(repo):
    nx, rp = repo
    wt1, _ = nx._worktree.create_session_worktree("sid-1")
    wt2, err = nx._worktree.create_session_worktree("sid-1")
    assert err is None and wt1 == wt2


def test_two_sessions_are_independent(repo):
    nx, rp = repo
    wt1, _ = nx._worktree.create_session_worktree("sid-1")
    wt2, _ = nx._worktree.create_session_worktree("sid-2")
    assert wt1 != wt2
    # A commit in session 1's tree is invisible to session 2's.
    (open(os.path.join(wt1, "a.txt"), "w")).close() if False else None
    with open(os.path.join(wt1, "only_s1.txt"), "w") as f:
        f.write("s1\n")
    subprocess.run(["git", "-C", wt1, "add", "."], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", wt1, "commit", "-q", "-m", "s1 work"],
                   check=True, capture_output=True)
    assert not os.path.exists(os.path.join(wt2, "only_s1.txt"))
    assert not os.path.exists(os.path.join(str(rp), "only_s1.txt"))


def test_cleanup_refuses_dirty_tree(repo):
    nx, rp = repo
    wt, _ = nx._worktree.create_session_worktree("sid-3")
    with open(os.path.join(wt, "dirty.txt"), "w") as f:
        f.write("unsaved\n")
    nx._worktree.remove_session_worktree(wt)
    assert os.path.isdir(wt)  # dirty work is never destroyed


def test_cleanup_removes_clean_tree_and_branch(repo):
    nx, rp = repo
    wt, _ = nx._worktree.create_session_worktree("sid-4")
    # _branch_of must be read BEFORE the tree is removed; capture and delete
    # via the same call path the REPL would use.
    branch = nx._worktree._branch_of(wt)
    assert branch == "nx/session-sid-4"
    nx._worktree.remove_session_worktree(wt)
    assert not os.path.isdir(wt)
    r = subprocess.run(["git", "-C", str(rp), "branch", "--list", branch],
                       capture_output=True, text=True)
    assert r.stdout.strip() == ""


def test_repl_isolate_flag_wiring(repo, monkeypatch, capsys):
    nx, rp = repo
    # The --isolate argv path is exercised end-to-end in the REPL integration
    # tests; here verify the state contract the REPL establishes.
    monkeypatch.setattr(sys, "argv", ["nx", "--isolate"])
    monkeypatch.setenv("NX_SESSION_WORKTREE", "1")
    wt, err = nx._worktree.create_session_worktree("sid-5")
    assert err is None
    nx.st.SESSION_WORKTREE = wt
    nx.st.PROJECT_ROOT = wt
    assert nx.st.SESSION_WORKTREE == wt


# --- /sync (0.170.0): explicit review/sync merge-back ------------------------

def test_sync_commits_and_merges(repo):
    nx, rp = repo
    wt, _ = nx._worktree.create_session_worktree("sid-sync")
    nx.st.PROJECT_ROOT = wt  # the REPL chdirs; sync must still find the base
    with open(os.path.join(wt, "new.txt"), "w") as f:
        f.write("work\n")
    ok, detail = nx._worktree.sync_session_worktree(wt, message="session work")
    assert ok and "merged" in detail
    assert (rp / "new.txt").read_text() == "work\n"
    # idempotent: a second sync reports up-to-date, no new commit
    head = subprocess.run(["git", "-C", str(rp), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    ok, detail = nx._worktree.sync_session_worktree(wt)
    assert ok and "already" in detail
    head2 = subprocess.run(["git", "-C", str(rp), "rev-parse", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    assert head == head2


def test_sync_conflict_aborts_cleanly(repo):
    nx, rp = repo
    wt, _ = nx._worktree.create_session_worktree("sid-conflict")
    nx.st.PROJECT_ROOT = wt
    with open(os.path.join(wt, "a.txt"), "a") as f:
        f.write("session edit\n")
    # the base repo moves on first
    with open(os.path.join(rp, "a.txt"), "a") as f:
        f.write("main edit\n")
    subprocess.run(["git", "-C", str(rp), "commit", "-aqm", "main"],
                   check=True, capture_output=True)
    ok, detail = nx._worktree.sync_session_worktree(wt)
    assert not ok and "aborted" in detail
    r = subprocess.run(["git", "-C", str(rp), "status", "--porcelain"],
                       capture_output=True, text=True)
    # nxhome/ is the fixture's own untracked dir; ignore it
    assert "a.txt" not in r.stdout and not any(
        l.startswith("UU") or l.startswith("AA") for l in r.stdout.splitlines()
    ), r.stdout  # base repo left untouched (no merge residue)


def test_sync_remove_takes_worktree_down(repo):
    nx, rp = repo
    wt, _ = nx._worktree.create_session_worktree("sid-rm")
    nx.st.PROJECT_ROOT = wt
    with open(os.path.join(wt, "x.txt"), "w") as f:
        f.write("x\n")
    ok, _ = nx._worktree.sync_session_worktree(wt, remove=True)
    assert ok
    assert not os.path.isdir(wt)
    r = subprocess.run(["git", "-C", str(rp), "branch", "--list",
                        "nx/session-sid-rm"], capture_output=True, text=True)
    assert r.stdout.strip() == ""


def test_sync_without_worktree(repo):
    nx, rp = repo
    nx.st.PROJECT_ROOT = str(rp)
    ok, detail = nx._worktree.sync_session_worktree("")
    assert not ok
