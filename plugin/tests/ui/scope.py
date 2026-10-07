"""The store scope of one viewer request, for tests that call the reader
directly. The server's runner sets `config.checkpoint_dir_override` around
every route; `scoped(data_dir).project_ledger(slug)` sets it the same way
around one reader call, so a test never repeats the wrapping itself."""

from daimon_briefing import config
from daimon_ui import reader


class _Scoped:
    def __init__(self, data_dir):
        self._data_dir = data_dir

    def __getattr__(self, name):
        fn = getattr(reader, name)

        def call(*args, **kwargs):
            with config.checkpoint_dir_override(self._data_dir):
                return fn(*args, **kwargs)

        return call


def scoped(data_dir):
    return _Scoped(data_dir)
