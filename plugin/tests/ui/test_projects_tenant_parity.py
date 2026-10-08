"""#899 / #1132 PR 8a: the project listing is one rule on three hosts.

`daimon projects`, the MCP `daimon_projects` tool and the viewer's
`/api/projects` enumerate buckets through `view.buckets`, so a tenant-scoped
home lists the caller's own bucket and no other, and the viewer refuses to
open another tenant's slug."""

import json
import threading
import urllib.request

import pytest

from daimon_briefing import cli, mcp_tools, store
from daimon_ui import server

OWN, OTHER = "/p/A", "/p/B"


@pytest.fixture
def two_tenants(tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    store.write_checkpoint("S-a", sample_checkpoint, project_dir=OWN)
    store.write_checkpoint("S-b", sample_checkpoint, project_dir=OTHER)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", OWN)
    return store.project_slug(OWN), store.project_slug(OTHER)


@pytest.fixture
def viewer(tmp_checkpoint_dir, two_tenants):
    own, _other = two_tenants
    s = server.make_server(tmp_checkpoint_dir, own, "own", port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()
    s.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path) as resp:
        return json.loads(resp.read())


def _cli_slugs(capsys):
    assert cli.main(["projects", "--json"]) == 0
    return {r["slug"] for r in json.loads(capsys.readouterr().out)}


def _mcp_slugs():
    return {r["slug"] for r in json.loads(
        mcp_tools.HANDLERS["daimon_projects"]({}).text)}


def _viewer_slugs(base):
    return {r["slug"] for r in _get(base, "/api/projects")["projects"]}


def test_without_tenant_scope_all_three_list_every_bucket(
        two_tenants, viewer, capsys):
    both = set(two_tenants)
    assert _cli_slugs(capsys) == both
    assert _mcp_slugs() == both
    assert _viewer_slugs(viewer) == both


def test_tenant_scope_all_three_list_only_the_callers_own_bucket(
        two_tenants, viewer, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    own, _other = two_tenants
    assert _cli_slugs(capsys) == {own}
    assert _mcp_slugs() == {own}
    assert _viewer_slugs(viewer) == {own}


def test_tenant_scope_the_viewer_refuses_another_tenants_slug(
        two_tenants, viewer, monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    own, other = two_tenants
    assert _get(viewer, f"/api/history?project={other}").get("ok") is False
    assert _get(viewer, f"/api/history?project={own}").get("ok") is not False


def test_without_tenant_scope_the_viewer_opens_any_bucket(
        two_tenants, viewer):
    _own, other = two_tenants
    assert _get(viewer, f"/api/history?project={other}").get("ok") is not False
