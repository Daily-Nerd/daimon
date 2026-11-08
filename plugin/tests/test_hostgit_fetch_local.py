"""hostgit.fetch_local: a member clone's branch into the lead, sealed (#1308, slice 3).

Recorder tests assert the argv, env and cwd built here; real-git tests build a
lead and a member below a tmp ceiling. Tests use tmp dirs only.
"""

import os
import subprocess

import pytest

from daimon_briefing import hostgit
from tests.test_hostgit_config_gate import (  # noqa: F401  (fixtures)
    _clear, _fired, _marker_script, _plain, needs_git, spy)
from tests.test_hostgit_sealed import SEALED, SealedRecorder, fake_repo

PINS = {"GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL", "GIT_ATTR_NOSYSTEM", "GIT_TERMINAL_PROMPT",
        "GIT_OPTIONAL_LOCKS"}


@pytest.fixture
def home(tmp_path):
    ceiling = tmp_path / "home"
    ceiling.mkdir()
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    return ceiling


@pytest.fixture
def rec(monkeypatch):
    recorder = SealedRecorder()
    monkeypatch.setattr(hostgit, "_spawn", recorder)
    return recorder


@pytest.fixture
def pair(home, rec):
    """(lead, member) Checkouts of two fake repositories, recorder installed."""
    lead = hostgit.project(fake_repo(home / "lead"))
    member = hostgit.project(fake_repo(home / "member"))
    assert isinstance(lead, hostgit.Checkout)
    assert isinstance(member, hostgit.Checkout)
    rec.listings.clear()
    return lead, member


# ---- ref component rules ---------------------------------------------------

BAD_COMPONENTS = [
    ("", "empty"), ("-x", "leading '-'"), ("a..b", "'..'"),
    ("a//b", "'//'"), ("/a", "leading '/'"), ("a/", "trailing '/'"),
    ("a@{b", "'@{'"), ("a\\b", "backslash"), ("a b", "control or space"),
    ("a\tb", "control or space"), ("a\x7fb", "control or space"),
    ("a/.b", "component starting with '.'"), (".a", "component starting with '.'"),
    ("a.lock", "component ending with '.lock'"),
    ("a.lock/b", "component ending with '.lock'"), ("a.", "ends with '.'"),
    ("a~b", "forbidden character"), ("a^b", "forbidden character"),
    ("a:b", "forbidden character"), ("a?b", "forbidden character"),
    ("a*b", "forbidden character"), ("a[b", "forbidden character"),
    ("@", "'@' alone"),
]
GOOD_COMPONENTS = ["topic", "feat/x", "a-b_c.d", "v1.2.3", "alice", "x@y", "ü"]


def test_the_rule_table_is_printed_and_every_bad_input_names_its_rule():
    print("\nvalid_ref_component rejections:")
    for text, rule in BAD_COMPONENTS:
        got = hostgit.valid_ref_component(text)
        print(f"  {text!r:12} -> {got}")
        assert got is not None and rule in got, (text, got)


@pytest.mark.parametrize("text", GOOD_COMPONENTS)
def test_a_plain_component_is_valid(text):
    assert hostgit.valid_ref_component(text) is None


def test_a_non_string_is_not_a_component():
    assert hostgit.valid_ref_component(None) is not None  # type: ignore[arg-type]


@needs_git
@pytest.mark.parametrize("text", GOOD_COMPONENTS)
def test_what_we_accept_git_accepts_too(text):
    done = subprocess.run(
        ["git", "check-ref-format", f"refs/members/m/{text}"],
        capture_output=True, text=True, timeout=30)
    assert done.returncode == 0


@pytest.mark.parametrize("text, rule", BAD_COMPONENTS)
@pytest.mark.parametrize("which", ["branch", "member_name"])
def test_a_bad_ref_component_raises_before_any_spawn(pair, rec, which, text,
                                                     rule):
    lead, member = pair
    kwargs = {"branch": "topic", "member_name": "alice", which: text}
    with pytest.raises(ValueError, match=which) as raised:
        hostgit.fetch_local(lead, member, kwargs["branch"],
                            kwargs["member_name"])
    assert rule in str(raised.value)
    assert rec.calls == [] and rec.listings == []


# ---- argv, env, cwd ---------------------------------------------------------


def test_fetch_local_builds_the_sealed_argv_env_and_cwd(pair, rec):
    lead, member = pair
    hostgit.fetch_local(lead, member, "topic", "alice")
    (call,) = rec.calls
    assert call["argv"] == [
        "git", *hostgit._hardening(), "-c", "protocol.allow=never",
        "-c", "protocol.file.allow=always", "fetch", "--no-tags",
        "--recurse-submodules=no", "--no-write-fetch-head", "--no-auto-gc",
        "--", member.root, "+refs/heads/topic:refs/members/alice/topic"]
    assert call["env"] == hostgit._sealed_env(lead)
    assert {k for k in call["env"] if k.startswith("GIT_")} == PINS
    assert call["env"]["GIT_DIR"] == lead.gitdir
    scratch = hostgit._scratch_root()
    cwd = call["cwd"]
    assert os.path.commonpath([cwd, scratch]) == scratch
    for repo in (lead, member):
        assert os.path.commonpath([cwd, repo.root]) != repo.root
    assert os.listdir(cwd) == []
    assert call["timeout"] == hostgit.SIDECAR_TIMEOUT
    assert call["stdin"] == subprocess.DEVNULL
    assert rec.listings == []  # both verdicts were memoized by project()


def test_a_hang_is_rc_124(pair, rec):
    lead, member = pair
    rec.raises = subprocess.TimeoutExpired("git", 3)
    done = hostgit.fetch_local(lead, member, "b", "m", timeout=3)
    assert rec.calls[0]["timeout"] == 3
    assert done.returncode == 124 and "timed out" in done.stderr


# ---- what fetch_local refuses to be asked ------------------------------------


def test_same_repository_on_both_sides_raises(pair, rec):
    lead, _member = pair
    with pytest.raises(ValueError, match="same"):
        hostgit.fetch_local(lead, lead, "b", "m")
    assert rec.calls == [] and rec.listings == []


def test_fetch_local_under_ambient_raises(tmp_path, monkeypatch):
    rec = SealedRecorder()
    monkeypatch.setattr(hostgit, "_spawn", rec)
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    one = hostgit.Checkout(root=str(tmp_path / "a"), gitdir=str(tmp_path / "a"))
    two = hostgit.Checkout(root=str(tmp_path / "b"), gitdir=str(tmp_path / "b"))
    with pytest.raises(RuntimeError, match="sealed"):
        hostgit.fetch_local(one, two, "b", "m")
    assert rec.calls == [] and rec.listings == []


@pytest.mark.parametrize("bad", [
    hostgit.Refused("symlink"), "/some/path", object(), None])
@pytest.mark.parametrize("side", ["lead", "member"])
def test_a_non_checkout_on_either_side_raises(pair, rec, bad, side):
    lead, member = pair
    args = {"lead": lead, "member": member, side: bad}
    with pytest.raises(TypeError):
        hostgit.fetch_local(args["lead"], args["member"], "b", "m")
    assert rec.calls == [] and rec.listings == []


def test_a_checkout_without_a_gitdir_raises(pair, rec):
    lead, _member = pair
    with pytest.raises(TypeError):
        hostgit.fetch_local(lead, hostgit.Checkout(root="/x"), "b", "m")


@needs_git
@pytest.mark.parametrize("side", ["lead", "member"])
def test_a_regate_refusal_on_either_side_is_rc_125_and_spawns_no_fetch(
        home, spy, side):  # noqa: F811
    lead = hostgit.project(fake_repo(home / "lead"))
    member = hostgit.project(fake_repo(home / "member"))
    target = {"lead": lead, "member": member}[side]
    cfg = os.path.join(target.gitdir, "config")
    with open(cfg, "a", encoding="utf-8") as handle:
        handle.write('[core]\n\tpager = /bin/evil\n')
    spy.calls.clear()
    done = hostgit.fetch_local(lead, member, "b", "m")
    assert done.returncode == 125 and "config-key:core.pager" in done.stderr
    assert spy.calls == spy.listings  # the listing of the changed config only
    assert (target.root, "config-key:core.pager") in hostgit.refusals()


def test_a_member_whose_gitdir_changed_is_refused(pair, rec):
    lead, member = pair
    forged = hostgit.Checkout(root=member.root, gitdir=member.gitdir + "-x")
    done = hostgit.fetch_local(lead, forged, "b", "m")
    assert done.returncode == 125 and "checkout-changed" in done.stderr
    assert rec.calls == []


def test_no_scratch_directory_is_a_refusal_not_a_spawn(pair, rec, monkeypatch):
    lead, member = pair
    monkeypatch.setattr(hostgit, "_scratch_root", lambda: None)
    done = hostgit.fetch_local(lead, member, "b", "m")
    assert done.returncode == 125 and "scratch" in done.stderr
    assert rec.calls == []


# ---- real git ----------------------------------------------------------------


def _commit(repo, name):
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    assert _plain(repo, "add", name).returncode == 0
    assert _plain(repo, "commit", "-q", "-m", name).returncode == 0
    return _plain(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def clones(home):
    """(lead, member) real repositories: the member is a clone of the lead with
    a commit on `topic` and a tag; both pass the allowlist."""
    lead = home / "lead"
    lead.mkdir()
    assert _plain(lead, "init", "-q", "-b", "main").returncode == 0
    _commit(lead, "seed")
    member = home / "member"
    done = subprocess.run(
        ["git", "clone", "-q", str(lead), str(member)], capture_output=True,
        text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    assert done.returncode == 0, done.stderr
    assert _plain(member, "checkout", "-q", "-b", "topic").returncode == 0
    tip = _commit(member, "topic-file")
    assert _plain(member, "tag", "v1").returncode == 0
    return lead, member, tip


@needs_git
def test_the_member_branch_lands_under_refs_members_and_nothing_else(clones):
    lead, member, tip = clones
    lead_co, member_co = hostgit.project(lead), hostgit.project(member)
    assert isinstance(lead_co, hostgit.Checkout)
    assert isinstance(member_co, hostgit.Checkout)
    done = hostgit.fetch_local(lead_co, member_co, "topic", "alice", timeout=60)
    assert done.returncode == 0, done.stderr
    assert _plain(lead, "rev-parse",
                  "refs/members/alice/topic").stdout.strip() == tip
    assert _plain(lead, "tag", "--list").stdout.strip() == ""  # no tag arrived
    assert _plain(member, "tag", "--list").stdout.strip() == "v1"
    assert not (lead / ".git" / "FETCH_HEAD").exists()
    assert _plain(lead, "show-ref", "--verify", "--quiet",
                  "refs/heads/topic").returncode != 0
    assert _plain(lead, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


@needs_git
def test_a_later_fetch_forces_the_ref_forward(clones):
    lead, member, _tip = clones
    lead_co, member_co = hostgit.project(lead), hostgit.project(member)
    hostgit.fetch_local(lead_co, member_co, "topic", "alice", timeout=60)
    newer = _commit(member, "more")
    done = hostgit.fetch_local(lead_co, member_co, "topic", "alice", timeout=60)
    assert done.returncode == 0, done.stderr
    assert _plain(lead, "rev-parse",
                  "refs/members/alice/topic").stdout.strip() == newer


@needs_git
def test_a_missing_member_branch_is_a_failed_process_not_a_raise(clones):
    lead, member, _tip = clones
    done = hostgit.fetch_local(hostgit.project(lead), hostgit.project(member),
                               "nope", "alice", timeout=60)
    assert done.returncode != 0


@needs_git
def test_a_member_with_an_upload_pack_key_is_refused_at_the_gate(clones, spy,  # noqa: F811
                                                                tmp_path):
    lead, member, _tip = clones
    markers = tmp_path / "markers"
    markers.mkdir()
    hook = _marker_script(tmp_path / "hook", markers, "packhook", 'exec "$@"')
    lead_co, member_co = hostgit.project(lead), hostgit.project(member)
    cfg = member / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + f"[uploadpack]\n\tpackobjectshook = {hook}\n",
                   encoding="utf-8")
    spy.calls.clear()
    done = hostgit.fetch_local(lead_co, member_co, "topic", "alice", timeout=60)
    assert done.returncode == 125
    assert "config-key:uploadpack.packobjectshook" in done.stderr
    assert spy.calls == spy.listings  # listings only: the fetch never ran
    assert _fired(markers) == []
    # Observed on this git: uploadpack.packObjectsHook is read from protected
    # config only, so plain git ignores it in repository config. The gate
    # refuses the key regardless (the allowlist does not depend on that).
    plain = subprocess.run(
        ["git", "-C", str(lead), "fetch", "-q", "--no-tags", str(member),
         "+refs/heads/topic:refs/x/topic"], capture_output=True, text=True,
        timeout=60, env={k: v for k, v in os.environ.items()
                         if not k.startswith("GIT_")})
    assert plain.returncode == 0
    print("packObjectsHook fired under plain git:", _fired(markers))


@needs_git
def test_a_member_with_alternates_is_refused_before_any_fetch(clones, spy,  # noqa: F811
                                                              tmp_path):
    # Observed on git 2.50.1 (local fetch, protocol v0 and v2): neither
    # core.alternateRefsCommand nor uploadpack.packObjectsHook nor
    # core.hooksPath set in the MEMBER's repository config ran a program, so
    # there is no marker control to build; git reads those from protected
    # config only. The gate refuses them anyway (an allowlist does not depend
    # on one git version's behaviour), and an alternates file is a separate
    # hazard: it lets the member's object store borrow objects from any path.
    lead, member, _tip = clones
    lead_co, member_co = hostgit.project(lead), hostgit.project(member)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert _plain(elsewhere, "init", "-q").returncode == 0
    info = member / ".git" / "objects" / "info"
    info.mkdir(exist_ok=True)
    (info / "alternates").write_text(f"{elsewhere}/.git/objects\n",
                                    encoding="utf-8")
    spy.calls.clear()
    done = hostgit.fetch_local(lead_co, member_co, "topic", "alice", timeout=60)
    assert done.returncode == 125 and "alternates" in done.stderr
    assert spy.calls == []  # not even a listing: the file check comes first
    assert (member_co.root, "alternates") in hostgit.refusals()
