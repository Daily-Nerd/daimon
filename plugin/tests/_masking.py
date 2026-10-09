"""Shared set-up for the 11c masking tests: one project with a quarantined and a
forgotten value, and the human channel on stdin."""

import io

from daimon_briefing import cli, normalize, store, trust
from daimon_briefing.surfaces import Writer

PROJECT = "/p/masking-census"
SLUG = store.project_slug(PROJECT)
QUARANTINED = "SECRET-Q the quarantined claim of this test"
FORGOTTEN = "SECRET-F the forgotten claim of this test"
VISIBLE = "a plain visible sentence"


class Stdin(io.StringIO):
    def __init__(self, tty):
        super().__init__("")
        self._tty = tty

    def isatty(self):
        return self._tty


def human(monkeypatch, tty=True):
    monkeypatch.setattr(cli.sys, "stdin", Stdin(tty))
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")


def quarantine(text=QUARANTINED, kind="decision", evidence=("issue:1",)):
    return trust.propose(text=text, kind=kind, reason="r",
                         evidence=list(evidence), channel="cli-tty",
                         project_dir=PROJECT)


def forget(text=FORGOTTEN):
    """Forget `text` the way another project's `daimon forget` leaves it: a
    tombstone key, with rows written earlier left where they are."""
    assert store.append_event(
        "o-masking", "forgotten:" + normalize.content_key(text),
        kind="tombstone", tombstone=True, project_dir=PROJECT,
        writer=Writer.HUMAN)


def run(capsys, *argv):
    capsys.readouterr()
    rc = cli.main([*argv, "--project", PROJECT])
    out = capsys.readouterr()
    return rc, out.out, out.err
