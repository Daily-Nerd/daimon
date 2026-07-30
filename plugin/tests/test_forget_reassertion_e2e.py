"""#400: end-to-end deletion-durability guarantee for `daimon forget`.

A forgotten value must stay gone across a later re-extraction of the SAME
sentence — gone at the source (the checkpoint on disk), not merely withheld at
render. This is the value-keyed forget ledger's contract (#402); the test is
written to go RED against the pre-#402 code, where nothing consults the
forgotten hash at capture, so the re-extracted item is written straight back to
disk and only hidden at render.

Structure (issue #400 + its amendment): five blocks, every negative paired with
a same-block positive control (a never-forgotten twin Y + a hot keyword that
matches both), plus a derived-artifact probe against the rendered brief string,
the recall SQLite rows, and the team dual-write copy.
"""

import sqlite3
from datetime import datetime, timezone

from daimon_briefing import briefing, carry, cli, config, normalize, recall, store

# Fixed clock threaded into every now-consumer (scar 0016): the checkpoints
# below carry a simulated calendar, so briefing.build must read the SAME clock,
# never wall time — otherwise decay math (scoring._age_days) diverges.
_NOW = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()

_A = "/repo/forget-arc-A"
_B = "/repo/forget-arc-B"

# S is forgotten; T is the never-forgotten twin. "adopt" is the hot keyword —
# it matches BOTH, so the tombstone (not the query) is what separates them.
_S = "adopt sqlite for the recall index cache"
_T = "adopt postgres for the analytics warehouse"
_HOT = "adopt"


def _cp(sid, created, decisions):
    return {
        "session_id": sid,
        "created": created,
        "working_context": {
            "recent_decisions": [
                {"text": d, "trust": "inferred"} for d in decisions
            ]
        },
    }


def _decisions(cp):
    return [d["text"] for d in
            ((cp or {}).get("working_context") or {}).get("recent_decisions") or []]


def _brief_decisions(cp):
    b = briefing.build(cp, now=_NOW)
    return [d["text"] for d in (b["decisions"] if b else [])]


def _recall_texts(project_dir):
    recall.rebuild()
    return [h["text"] for h in recall.search(_HOT, project_dir=project_dir)]


def _resolved(project_dir):
    return frozenset(
        ref for ref, evt in store.resolutions(project_dir=project_dir).items()
        if store.is_resolved(evt))


def _carry_decisions(new_created, prev_cp, project_dir):
    new_cp = _cp("S-carry", new_created, ["carry probe decision only"])
    merged = carry.merge(new_cp, prev_cp, now=0.0, resolved=_resolved(project_dir))
    return _decisions(merged)


def _latest_raw(project_dir):
    slug = store.project_slug(project_dir)
    return (config.checkpoint_dir() / slug / "latest.json").read_text(encoding="utf-8")


def test_forgotten_value_stays_gone_across_reassertion(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")

    # --- Block 1: pre-state positive control (serialize #1) -----------------
    store.write_checkpoint("S1", _cp("S1", "2026-07-01T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    stored1 = store.read_latest(project_dir=_A, fallback=False)
    x_id = next(d["id"] for d in stored1["working_context"]["recent_decisions"]
                if d["text"] == _S)

    assert _S in _brief_decisions(stored1) and _T in _brief_decisions(stored1)
    c1 = _carry_decisions("2026-07-02T00:00:00Z", stored1, _A)
    assert _S in c1 and _T in c1
    r1 = _recall_texts(_A)
    assert _S in r1 and _T in r1

    # --- Block 2: mutation --------------------------------------------------
    assert cli.main(["forget", x_id]) == 0
    res = store.resolutions(project_dir=_A)
    assert x_id in res and res[x_id]["status"].startswith("forgotten:")

    # --- Block 3: negative + liveness control, same block (serialize #2) ----
    # A fresh session re-extracts S and T verbatim into the same field.
    store.write_checkpoint("S2", _cp("S2", "2026-07-03T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    stored2 = store.read_latest(project_dir=_A, fallback=False)

    # id stability is the guarantee: the re-extracted S keys to the SAME id.
    probe = _cp("probe", "2026-07-03T00:00:00Z", [_S])
    store._stamp_item_ids(probe)
    assert probe["working_context"]["recent_decisions"][0]["id"] == x_id

    # negatives + liveness — briefing, carry, recall
    bd2 = _brief_decisions(stored2)
    assert _S not in bd2                       # gone
    assert _T in bd2                           # liveness (twin still present)
    c2 = _carry_decisions("2026-07-04T00:00:00Z", stored2, _A)
    assert _S not in c2 and _T in c2
    r2 = _recall_texts(_A)
    assert _S not in r2 and _T in r2

    # X's text absent from the LIVE checkpoint on disk — gone, not withheld.
    raw2 = _latest_raw(_A)
    assert _S not in raw2
    assert _T in raw2

    # Derived-artifact probe: rendered brief string, recall SQLite rows.
    rendered = briefing.render(stored2)
    assert rendered is not None
    assert _S not in rendered and _T in rendered
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        rows = [r[0] for r in conn.execute("SELECT text FROM items").fetchall()]
    finally:
        conn.close()
    assert _S not in rows and _T in rows

    # --- Block 4: scope control --------------------------------------------
    # Project B has forgotten nothing; it must retrieve its own S, and A's
    # denial must survive B's write (forget is scoped per project).
    store.write_checkpoint("SB", _cp("SB", "2026-07-05T00:00:00Z", [_S]),
                           project_dir=_B)
    recall.rebuild()
    assert _S in [h["text"] for h in recall.search(_HOT, project_dir=_B)]
    assert _S in _latest_raw(_B)                      # B keeps it on disk
    assert _S not in [h["text"] for h in recall.search(_HOT, project_dir=_A)]

    # --- Block 5: durability across index rebuild + a third carried session -
    recall.rebuild()
    store.write_checkpoint("S3", _cp("S3", "2026-07-06T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    stored3 = store.read_latest(project_dir=_A, fallback=False)
    assert _S not in _brief_decisions(stored3) and _T in _brief_decisions(stored3)
    c3 = _carry_decisions("2026-07-07T00:00:00Z", stored3, _A)
    assert _S not in c3 and _T in c3
    r3 = _recall_texts(_A)
    assert _S not in r3 and _T in r3
    assert _S not in _latest_raw(_A)

    # The forgotten hash the ledger keys on is the CANONICAL value key (#402/#403).
    assert res[x_id]["status"] == f"forgotten:{normalize.content_key(_S)}"


def test_forget_dual_write_copy_never_carries_forgotten_value(tmp_checkpoint_dir,
                                                              monkeypatch):
    """Derived-artifact probe, team mirror: a forgotten value must be absent
    from the dual-write remote copy too — the drop happens before the mirror."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_TEAM", "1")

    store.write_checkpoint("T1", _cp("T1", "2026-07-01T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    stored = store.read_latest(project_dir=_A, fallback=False)
    x_id = next(d["id"] for d in stored["working_context"]["recent_decisions"]
                if d["text"] == _S)
    assert cli.main(["forget", x_id]) == 0

    store.write_checkpoint("T2", _cp("T2", "2026-07-03T00:00:00Z", [_S, _T]),
                           project_dir=_A)

    # Any team mirror file must carry T but never S.
    team_blobs = [p.read_text(encoding="utf-8")
                  for p in config.team_dir().rglob("*.json")]
    joined = "\n".join(team_blobs)
    assert _T in joined          # mirror is live (control)
    assert _S not in joined      # forgotten value never reached the remote copy
