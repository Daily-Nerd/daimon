---
id: 0
type: landmine
title: A ContextVar override set in the main thread does not reach ThreadingHTTPServer request threads
severity: high
confidence: 0.9
created: 2026-10-07
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_ui/server.py
  - path: plugin/daimon_ui/__main__.py
evidence:
  - note: "tests/ui/test_server_routes.py::test_the_request_thread_sees_the_data_dir_as_the_store and test_a_context_override_set_in_the_main_thread_does_not_reach_a_thread"
expires:
  condition: "the viewer stops using ThreadingHTTPServer, or config scopes the store by something other than a ContextVar"
  review_after: 2027-04-07
status: candidate
---

`config.checkpoint_dir_override` is a `ContextVar`. A thread started from the
main thread begins with an empty context, so it answers `config.checkpoint_dir()`
from the environment, not from the override the main thread set. The viewer's
`ThreadingHTTPServer` runs every request in a fresh thread, so an override
applied once in `daimon_ui.__main__` does nothing: the engines (recall, why,
refutations, relations) and the `view` accessors would read the default store
while the reader reads `--data-dir`.

The viewer's request runner (`_Handler.do_GET`) enters
`config.checkpoint_dir_override(self.data_dir)` inside the handler thread, around
the route's handler, so the override lives exactly as long as one request. Any
new per-request store scope must be set there, not in `main()`, and the test
must request through a real server thread with the main thread holding no
override, or it passes vacuously.
