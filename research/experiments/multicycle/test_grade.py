import seed
import grade


def _rows_by_item(cp, cycle, arm="control"):
    return {r["item"]: r for r in grade.grade_checkpoint(cp, cycle, arm)}


def test_grades_pristine_seed_as_full_survival():
    rows = _rows_by_item(seed.make_seed(), cycle=0)
    assert len(rows) == len(seed.NONCES)
    for r in rows.values():
        assert r["survived"] is True
        assert r["integrity"] == 1.0
        assert r["stale"] is False


def test_detects_loss_and_rewording():
    cp = seed.make_seed()
    # kill FILLER-1, reword OQ-STABLE (nonce kept, text changed)
    decs = cp["working_context"]["recent_decisions"]
    cp["working_context"]["recent_decisions"] = [
        d for d in decs if "plimsol" not in d["text"]]
    cp["working_context"]["open_questions"][0]["text"] = (
        "quorint-ledger reconciliation still unresolved")
    rows = _rows_by_item(cp, cycle=3)
    assert rows["FILLER-1"]["survived"] is False
    assert rows["OQ-STABLE"]["survived"] is True
    assert 0 < rows["OQ-STABLE"]["integrity"] < 1.0


def test_staleness_only_counts_after_flip():
    cp = seed.make_seed()  # still holds V1
    before = _rows_by_item(cp, cycle=seed.FLIP_CYCLE - 1)
    after = _rows_by_item(cp, cycle=seed.FLIP_CYCLE)
    assert before["FACT-EVOLVE"]["stale"] is False
    assert after["FACT-EVOLVE"]["stale"] is True  # V1 present at/after flip


def test_current_value_after_flip_not_stale():
    cp = seed.make_seed()
    decs = cp["working_context"]["recent_decisions"]
    decs[0]["text"] = decs[0]["text"].replace(seed.V1_TOKEN, seed.V2_TOKEN)
    rows = _rows_by_item(cp, cycle=seed.FLIP_CYCLE + 2)
    assert rows["FACT-EVOLVE"]["survived"] is True
    assert rows["FACT-EVOLVE"]["stale"] is False


def test_evolution_noted_form_is_not_stale():
    cp = seed.make_seed()
    decs = cp["working_context"]["recent_decisions"]
    decs[0]["text"] = (f"Chunk threshold revised from {seed.V1_TOKEN} to "
                        f"{seed.V2_TOKEN}, old value superseded")
    rows = _rows_by_item(cp, cycle=seed.FLIP_CYCLE + 1)
    assert rows["FACT-EVOLVE"]["survived"] is True
    assert rows["FACT-EVOLVE"]["stale"] is False


def test_summarize_renders_all_items():
    rows = grade.grade_checkpoint(seed.make_seed(), 0, "control")
    out = grade.summarize(rows)
    for key in seed.NONCES:
        assert key in out
