"""Packaged copies of the standalone host hook scripts (#43).

Canonical sources live in the repo's hook/ directory (the Claude Code plugin
and manual installs read them there); these copies ship in the PyPI wheel so
`daimon hooks install <host>` works without a repo clone. A byte-equality
test (tests/test_hooks_install.py) guards against drift — edit hook/<name>
and copy it here in the same change.
"""
