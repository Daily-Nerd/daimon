# handsal contract test data

`acts.json` holds two decisions for the consumer `daimon` (a `ratify` of a
ruling and an `accept` of a request), signed by handsal's fixture key `alice`,
and the conformance inputs they are checked under (lineage, org, pin file,
now). `tests/test_handsal_contract.py` reads it.

The acts are valid on handsal's CONFORMANCE checker build with the FIXTURE
profile only. They prove nothing about a production install.

They were made with handsal's own vector generator at tag `v0.4.2`, commit
`09dc029c2d738c4346d05ea7bf2d9f57b338eb61` (`authority.HANDSAL_PIN.commit`,
also recorded in `acts.json`). The generator is repository code, not an
interface handsal promises to keep, so a new pin means making the acts again.

To reproduce them, from a clone of handsal (read only, nothing is checked out
there), run:

    H=$(mktemp -d) && git -C <handsal clone> archive 09dc029c2d738c4346d05ea7bf2d9f57b338eb61 | tar -x -C "$H" && uv run --project "$H/conformance/vectors/_gen" --frozen python -I plugin/tests/handsal_contract_data/make_acts.py --handsal "$H" --commit 09dc029c2d738c4346d05ea7bf2d9f57b338eb61

Add `--check` to compare with the committed file instead of writing it. The
signing is deterministic, so a clean run changes nothing. Neither pytest nor CI
runs this.

Running the test by hand needs a real `vitni-verify` (never handsal's stand-in
`fake_vitni_cli.py`, which accepts tampered signatures) and the checker:

    HANDSAL_VITNI=/abs/path/to/vitni-verify \
    HANDSAL_CHECK_CONFORMANCE="uvx --from <exported tree>/checker/python handsal-check-conformance" \
    uv run --extra dev --extra pretty pytest -q tests/test_handsal_contract.py

CI sets both variables in the `pytest (py3.13)` job (`tests/_handsal_contract.py`).
