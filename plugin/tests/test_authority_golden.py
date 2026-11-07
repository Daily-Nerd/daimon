"""#1301: the authority seam moves the rule, it does not change it.

Every ledger fold is run over a fixed matrix of rows: for every pair of
channels (founding channel x follow-up channel) a family of entities whose
third event takes every channel again. The channel list covers the valid
channels of each ledger, the channels another ledger owns, mechanical, an
unknown string, a non-string and a missing channel. The output of each
group is hashed and compared with `golden/authority_folds.json`, generated
from the code BEFORE the seam existed, so the comparison is against the old
rule and not against a copy of the new one.

Rows are consistent: the stored `authority` is what the channel carried, and
absent for a channel the ledger does not know. That keeps the six display
readers (which now derive the tier from the channel instead of trusting the
stored field) out of the comparison; the rows where the two differ are pinned
in `test_authority_display_readers.py`.

Regenerate only from a tree whose fold behaviour is the intended reference:
`DAIMON_WRITE_GOLDEN=1 uv run pytest tests/test_authority_golden.py`.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from daimon_briefing import (amendments, capture, config, refutations, relations,
                             requests, store, trust)
from daimon_briefing.cli import stats
from daimon_briefing.surfaces import Writer

GOLDEN = Path(__file__).parent / "golden" / "authority_folds.json"

# What each channel carried when this matrix was written. Literal on purpose:
# the golden must not be derived from the table under test.
_TIER = {"cli-agent": "agent", "cli-tty": "human", "ui": "human",
         "signed": "human", "mechanical": "mechanical",
         "serializer": "agent", "lab-import": "agent"}
_BASE = {"cli-agent", "cli-tty", "ui", "signed"}
_VALID = {
    "refutations": _BASE | {"mechanical"},
    "requests": _BASE | {"mechanical"},
    "amendments": _BASE | {"mechanical"},
    "trust": _BASE,
    "relations": _BASE | {"serializer", "lab-import"},
}
_MISSING = "<missing>"
CHANNELS = ["cli-agent", "cli-tty", "ui", "signed", "mechanical",
            "serializer", "lab-import", "weird", 5, _MISSING]

_N = {"n": 0}


def _tag(channel):
    return "none" if channel == _MISSING else str(channel)


def _row(module, ledger, id_key, event, ident, channel, payload):
    _N["n"] += 1
    n = _N["n"]
    stamp_channel = channel if channel in _VALID[ledger] else "cli-tty"
    row = module._stamp(event, ident, stamp_channel,
                        now_ns=1_700_000_000_000_000_000 + n * 1_000_000,
                        event_id=f"e{n:08d}")
    row["author"] = "tester"
    row.update(payload)
    if channel == _MISSING:
        row.pop("channel")
        row.pop("authority")
    elif channel in _VALID[ledger]:
        assert row["authority"] == _TIER[channel]
    else:
        row["channel"] = channel
        row.pop("authority")
    return row


def _ident(prefix, index, width=12):
    return f"{prefix}{index:0{width}x}"


def _group(module, ledger, prefix, id_key, founders, middle, followups,
           c1, c2, width=12):
    rows = []
    index = 0
    for fev, fpayload in founders:
        for ev, payload in followups:
            for c3 in CHANNELS:
                index += 1
                ident = _ident(prefix, index, width)
                rows.append(_row(module, ledger, id_key, fev, ident, c1,
                                 fpayload))
                if middle is not None:
                    rows.append(_row(module, ledger, id_key, middle[0], ident,
                                     c2, middle[1]))
                rows.append(_row(module, ledger, id_key, ev, ident, c3,
                                 payload))
    return rows


_RULING_BODY = {"subject": "s", "verdict": "v", "scope": "tests",
                "anchors": ["a.py"], "revisit_when": "never",
                "evidence": ["issue:1"], "note": "n", "ratified": True}
_REQ_BODY = {"to": "p-recipient", "ask": "do it", "why": "because",
             "_origin_slug": "p-sender", "under_ruling": "r-0000000000aa",
             "policy_sha256": "s" * 64, "note": "n", "evidence": "quote"}
_ACCEPT_POLICY = ("p-sender", "", "work", "accept", "agent",
                  "r-0000000000aa", "s" * 64, 0, None)
_OPEN_POLICY = ("", "p-recipient", "info", "open", "agent",
                "r-0000000000aa", "s" * 64, 0, None)

SPECS = {
    "refutations": dict(
        module=refutations, prefix="r-", id_key="refutation_id",
        founders=[("asserted", _RULING_BODY), ("ruled", _RULING_BODY)],
        middle=("ratified", {"note": "ok"}),
        followups=[(e, dict(_RULING_BODY)) for e in (
            "ratified", "activated", "revised", "revision-proposed",
            "overturn-proposed", "overturned")],
        fold=lambda rows: refutations.fold(rows)),
    "requests": dict(
        module=requests, prefix="q-", id_key="request_id",
        founders=[("opened", dict(_REQ_BODY, kind="work")),
                  ("opened", dict(_REQ_BODY, kind="info"))],
        middle=("accepted", dict(_REQ_BODY)),
        followups=[(e, dict(_REQ_BODY)) for e in (
            "accepted", "rejected", "needs_info", "done", "replied",
            "revised", "suppressed", "done_verified", "surfaced",
            "verdict_surfaced", "delivered")],
        fold=lambda rows: requests.fold(
            rows, policies=frozenset({_ACCEPT_POLICY}), own_slug="p-recipient",
            open_policies={"p-sender": frozenset({_OPEN_POLICY})})),
    "amendments": dict(
        module=amendments, prefix="a-", id_key="amendment_id",
        founders=[("proposed", {"change": "progressed", "item_id": "o-1",
                                "evidence": "e", "note": "n",
                                "ratified": True})],
        middle=("verified", {"evidence_role": "role"}),
        followups=[(e, {"change": "blocked", "item_id": "o-1",
                        "evidence": "e", "note": "n2", "ratified": True,
                        "evidence_role": "role"}) for e in (
            "proposed", "verified", "ratified", "rejected")],
        fold=lambda rows: amendments.fold(rows)),
    "trust": dict(
        module=trust, prefix="tr-", id_key="quarantine_id",
        founders=[("quarantined", {"kind": sorted(trust.KINDS)[0],
                                   "value_key": "k", "item_id": "o-1",
                                   "scope_slug": "p-x", "reason": "r",
                                   "evidence": ["issue:1"],
                                   "ratified": True})],
        middle=("confirmed", {}),
        followups=[(e, {"kind": sorted(trust.KINDS)[0], "value_key": "k",
                        "ratified": True}) for e in (
            "quarantined", "confirmed", "dismissed", "released")],
        fold=lambda rows: trust.fold(rows)),
    "relations": dict(
        module=relations, prefix="rel-", id_key="relation_id", width=16,
        founders=[("proposed", {"type": "answers", "matcher_version": "m1",
                                "matched_by": ["x"],
                                "from": {"session_id": "s1", "item_id": "i1"},
                                "to": {"session_id": "s2", "item_id": "i2"}})],
        middle=("confirmed", {}),
        followups=[(e, {"type": "answers", "matcher_version": "m2",
                        "matched_by": ["y"]}) for e in (
            "proposed", "confirmed", "rejected", "retracted")],
        fold=lambda rows: relations.fold(rows)),
}


def _digest(value):
    blob = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _ledger_digests(name):
    spec = SPECS[name]
    out = {}
    for c1 in CHANNELS:
        for c2 in CHANNELS:
            _N["n"] = 0
            rows = _group(spec["module"], name, spec["prefix"], spec["id_key"],
                          spec["founders"], spec["middle"], spec["followups"],
                          c1, c2, spec.get("width", 12))
            out[f"{_tag(c1)}|{_tag(c2)}"] = _digest(spec["fold"](rows))
    return out


_SOURCES = ["cli", "cli-tty", "ui", "agent", "serializer", "weird", ""]


def _events_digest(project):
    n = 0
    for source in _SOURCES:
        for kind in ("resolution", "tombstone", "log"):
            n += 1
            store.append_event(f"o-{n:012x}", "resolved", kind=kind,
                               source=source, project_dir=project,
                               writer=Writer.HUMAN)
        n += 1
        store.append_event(f"o-{n:012x}", capture.AGENT_VERIFIED_STATUS,
                           kind="resolution", source=source,
                           project_dir=project, writer=Writer.HUMAN)
    credit = stats._stats_resolutions(project, {})
    return _digest({k: credit[k] for k in
                    ("human", "agent_verified", "agent_pending")})


@pytest.fixture
def _pinned_author(monkeypatch):
    monkeypatch.setattr(config, "author", lambda: "tester")


def _compute(project):
    got = {name: _ledger_digests(name) for name in SPECS}
    got["events"] = {"credit": _events_digest(project)}
    return got


def test_every_fold_is_byte_identical_to_the_pre_seam_rule(
        _pinned_author, tmp_checkpoint_dir):
    got = _compute("/p/authority-golden")
    if os.environ.get("DAIMON_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(got, indent=0, sort_keys=True) + "\n")
    want = json.loads(GOLDEN.read_text())
    drifted = sorted(f"{ledger}:{group}"
                     for ledger, groups in want.items()
                     for group, digest in groups.items()
                     if got.get(ledger, {}).get(group) != digest)
    assert drifted == []
    assert got.keys() == want.keys()
