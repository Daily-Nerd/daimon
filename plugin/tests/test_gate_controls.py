"""#944, rescoped: a gate constant must be proven able to BOTH fire and stay silent.

The original shape was a registry of deterministic gate METRICS. The inventory
killed it: daimon has exactly two such metrics and both already carry
disagreeing controls, so the registry would have refused nothing and reported
a clean sweep, which is the defect it existed to prevent.

The population that actually failed is different. Of six one-directional-health
failures on 2026-09-05, the one living in this repo was a test that asserted no
residue using the same enumeration the code under test walks, so it could not
fail for a missing case no matter how many were missing (#620, fixed in #945).

So the rule is aimed at the tests, not at a runtime registry: every threshold
that gates a rendered warning must be named here, with a control that produces
a DIFFERENT outcome on each side of it. A control that only ever shows the
firing case passes against a gate wired to fire always; one that only ever
shows silence passes against a gate wired to fire never. Only the pair
separates a live gate from either dead one.

The discovery half is what gives this teeth: the constants are found by
scanning the source, so adding a new `_*_GATE_*` without a control fails here
rather than shipping unproven.
"""
import re
from pathlib import Path

import pytest

from daimon_briefing import render

_SRC = Path(__file__).parent.parent / "daimon_briefing"

# A threshold whose whole job is to gate a rendered warning. Deliberately
# narrow: `GATE` in the name is the declaration that something is judged
# against it. Widening this pattern widens the obligation below, which is the
# intended direction if more gates appear.
_GATE_CONST_RE = re.compile(r"^(_[A-Z0-9_]*GATE[A-Z0-9_]*)\s*=", re.M)


def _declared_gate_constants() -> set[str]:
    found: set[str] = set()
    for path in sorted(_SRC.glob("*.py")):
        found |= set(_GATE_CONST_RE.findall(path.read_text(encoding="utf-8")))
    return found


def _window(**over) -> dict:
    base = {"days": 14, "success": 10, "skipped": 0, "errors": 0,
            "fallback_attempts": 0, "fallback_serializes": 0, "starved": 0,
            "error_rate_pct": 0.0}
    base.update(over)
    return base


def _fired(marker: str, w: dict) -> bool:
    # NOTE: the renderer takes the STATS dict and reads ["window"] out of it,
    # returning [] for anything without that key. Passing the bare window here
    # made every "must stay silent" case pass for the wrong reason: no lines
    # because the shape was wrong, not because the gate held. The disagreement
    # assertion below is what surfaced it. Keep the wrapper.
    return any(marker in ln
               for ln in render._capture_window_lines({"window": w}))


# metric name -> (marker, window that MUST fire, window that MUST stay silent)
CONTROLS: dict = {
    "_CAPTURE_ERROR_GATE_PCT": (
        "capture error rate",
        _window(errors=5, success=5, error_rate_pct=50.0),
        _window(errors=0, success=10, error_rate_pct=0.0),
    ),
    "_RESCUE_GATE_PCT": (
        "rescue succeeded",
        _window(fallback_attempts=3, fallback_serializes=1),
        _window(fallback_attempts=2, fallback_serializes=1),
    ),
}


def test_every_gate_constant_is_named_by_a_control():
    """The teeth. A new `_*_GATE_*` constant fails here until someone proves
    it can both fire and stay silent, rather than shipping unproven."""
    missing = _declared_gate_constants() - set(CONTROLS)
    assert not missing, (
        f"gate constant(s) with no disagreeing control: {sorted(missing)}. "
        "Add an entry to CONTROLS showing one input that fires it and one "
        "that does not.")


def test_the_scan_finds_the_constants_we_know_exist():
    """Negative control on the DISCOVERY half. If the regex silently stopped
    matching, the obligation above would pass by finding nothing, which is
    the failure mode this whole file is about."""
    found = _declared_gate_constants()
    assert "_CAPTURE_ERROR_GATE_PCT" in found
    assert "_RESCUE_GATE_PCT" in found
    assert "_DECAY_FLOOR" not in found, "the pattern must stay narrow"


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_each_gate_actually_fires_on_its_firing_case(name):
    marker, firing, _ = CONTROLS[name]
    assert _fired(marker, firing), f"{name} never fires; a gate wired off"


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_each_gate_actually_stays_silent_on_its_silent_case(name):
    marker, _, silent = CONTROLS[name]
    assert not _fired(marker, silent), f"{name} always fires; a gate wired on"


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_the_two_cases_disagree(name):
    """States the point directly: a control whose two sides agree proves
    nothing, however many cases it lists."""
    marker, firing, silent = CONTROLS[name]
    assert _fired(marker, firing) != _fired(marker, silent), \
        f"{name}'s control does not disagree with itself"
