"""#1301: the six display readers derive the tier from the row's channel.

They used to show the `authority` value stored on the row. A row written
with `channel: cli-agent` and `authority: human` therefore rendered as human
in those places while every fold treated it as agent. The tier is the
channel's answer (`authority.of_row`), never the row's claim about itself.
"""
from daimon_briefing import amendments, refutations, requests, trust

FORGED = {"channel": "cli-agent", "authority": "human", "author": "x"}
NS = 1_700_000_000 * 10**9


def _row(module, event, ident, **extra):
    row = module._stamp(event, ident, "cli-agent", now_ns=NS, event_id="e1")
    row.update(FORGED)
    row.update(extra)
    return row


def test_amendments_proposed_by_follows_the_channel():
    row = _row(amendments, "proposed", "a-000000000001", change="blocked",
               item_id="o-1", evidence="e", note="n")
    record = amendments.fold([row])["a-000000000001"]
    assert record["proposed_by"] == "agent"


def test_trust_proposed_by_follows_the_channel():
    row = _row(trust, "quarantined", "tr-000000000001",
               kind=sorted(trust.KINDS)[0], value_key="k", reason="r")
    record = trust.fold([row])["tr-000000000001"]
    assert record["proposed_by"] == "agent"


def test_ruling_asserted_by_follows_the_channel():
    row = _row(refutations, "asserted", "r-000000000001", subject="s",
               verdict="v", scope="tests")
    record = refutations.fold([row])["r-000000000001"]
    assert record["asserted_by"] == "agent"


def _active_ruling():
    founder = refutations._stamp("asserted", "r-000000000002", "cli-tty",
                                 now_ns=NS, event_id="e0")
    founder.update(subject="s", verdict="v", scope="tests", ratified=True)
    return founder


def test_a_proposed_revision_is_attributed_by_channel():
    row = _row(refutations, "revision-proposed", "r-000000000002",
               subject="s2", verdict="v2")
    row["order"] = _active_ruling()["order"] + 1
    record = refutations.fold([_active_ruling(), row])["r-000000000002"]
    assert record["state"] == "active"
    assert record["revision_proposed"]["by"] == "agent"


def test_a_proposed_overturn_is_attributed_by_channel():
    row = _row(refutations, "overturn-proposed", "r-000000000002")
    row["order"] = _active_ruling()["order"] + 1
    record = refutations.fold([_active_ruling(), row])["r-000000000002"]
    assert record["overturn_proposed"]["by"] == "agent"


def test_the_reply_label_follows_the_channel():
    reply = {"note": "halfway", "channel": "cli-agent", "authority": "human",
             "revision": 0}
    line = requests.latest_reply_line({"replies": [reply], "revision": 0})
    assert line.startswith("Reply (agent, unverified):")


def test_a_human_channel_still_reads_human_when_the_stored_field_lies():
    row = _row(amendments, "proposed", "a-000000000002", change="blocked",
               item_id="o-1", evidence="e", note="n")
    row.update(channel="cli-tty", authority="agent")
    assert amendments.fold([row])["a-000000000002"]["proposed_by"] == "human"
    reply = {"note": "n", "channel": "cli-tty", "authority": "agent",
             "revision": 0}
    assert requests.latest_reply_line(
        {"replies": [reply], "revision": 0}).startswith("Reply:")
