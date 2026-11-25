"""Make the signed acts the handsal contract test reads.

Not run by pytest or CI. It signs two decisions for the consumer `daimon` with
handsal's own vector generator and fixture keys, from a checkout of handsal at
the pinned commit, and writes `acts.json` beside this file. See README.md for
the one command. Run it with the generator's own project, never daimon's:

    uv run --project <handsal>/conformance/vectors/_gen --frozen \
        python -I make_acts.py --handsal <handsal> [--check]

The generator is handsal repository code, not an interface it promises to
keep. The tree must be the export of the pinned commit (`git archive`); the
caller names that commit with `--commit` and the output records it. The
contract test fails when the recorded commit is not `HANDSAL_PIN.commit`.
"""
import argparse
import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "acts.json"

# The rows `authority.build_decision` is given by the contract test. The ids
# are synthetic; the text is what a person would decide on.
RULING_ROW = {
    "refutation_id": "r-0123456789ab",
    "verdict": "Keep the retry limit at three.\nRevisit it when the queue changes.",
}
REQUEST_ROW = {
    "request_id": "q-0123456789ab",
    "ask": "Review the wording of the signing screen.",
}

# (case, verb, row, id field, text field), the table `authority` keeps.
CASES = (
    ("ratify", "ratify", RULING_ROW, "refutation_id", "verdict"),
    ("accept", "accept", REQUEST_ROW, "request_id", "ask"),
)


def build(tree, commit):
    gen = tree / "conformance" / "vectors" / "_gen"
    sys.path.insert(0, str(gen))
    from lib import base, inputs  # noqa: PLC0415  (handsal's generator)

    fx = base.Fixtures(base.fixture_profile())
    acts = {}
    view = None
    for case, verb, row, id_field, text_field in CASES:
        content = row[text_field].encode("utf-8")
        jws = fx.decision(
            "alice", consumer="daimon", action=verb, subject=row[id_field],
            content=content)
        full = inputs.check_input(
            fx, jws, method=inputs.DECISION, consumer="daimon", action=verb,
            subject=row[id_field],
            decision_b64=base64.b64encode(content).decode("ascii"))
        # What every act shares: the pinned lineage, the org, the pin file and
        # the moment. What belongs to one act is written per case.
        shared = {k: v for k, v in full.items() if k not in (
            "jws", "action", "consumer", "decision_b64", "subject",
            "expect_method")}
        assert view is None or view == shared
        view = shared
        acts[case] = {"verb": verb, "row": row, "jws": jws}
    return {
        "handsal_commit": commit,
        "profile": "fixture",
        "expect_method": inputs.DECISION,
        "view": view,
        "cases": acts,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--handsal", required=True, type=Path)
    ap.add_argument("--commit", required=True,
                    help="the commit the tree must be (the pin)")
    ap.add_argument("--check", action="store_true",
                    help="compare with acts.json instead of writing it")
    args = ap.parse_args()
    tree = args.handsal.resolve()
    # An exported tree has no .git, so the commit is the caller's word.
    data = build(tree, args.commit)
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=True) + "\n"
    if args.check:
        same = OUT.read_text(encoding="utf-8") == text
        print("acts.json is reproduced" if same else "acts.json DIFFERS")
        return 0 if same else 1
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.name}: {len(text)} bytes at {args.commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
