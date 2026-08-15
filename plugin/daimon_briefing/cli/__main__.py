"""`python -m daimon_briefing.cli` — the runnable-module spelling the old
single-file cli.py supported via its `__name__ == "__main__"` block (#708)."""

from . import main

raise SystemExit(main())
