"""Census: ONE owner names every bucket (slice 4a of #1384).

`store.bucket_of` is the owner of a bucket name, `store.bucket_dir` of a bucket
path, `identity.claim` of the binding of a location to a name for one operation.
Four ways a site can name a bucket without the owner are pinned here, each by
the shape the package actually uses:

1. a call to `project_slug` (the pure transform of a location) outside the
   owner: such a site derives the name from the LOCATION, so the day the owner
   answers from a recorded root it would keep writing the old bucket;
2. a call to `location_slug` (the pure seed) outside the sites that need a seed
   and nothing else;
3. a join of `checkpoint_dir()` (directly or through a name bound from it) with
   a bucket name outside the owner and the flat, global and enumeration sites;
4. a `BucketRef(...)` built anywhere but `identity.py`, which would be a ref
   nobody claimed;

and one way a failure can be swallowed:

5. a broad `except` around a call that reaches a claim or an `ensure_bucket`,
   which would absorb `identity.BucketMismatch` / `BucketClaimFailed`.

A new site fails here until it is converted, or listed WITH its reason.
"""

import ast
import collections

from .test_read_layers import PLUGIN

PKG = PLUGIN / "daimon_briefing"


def _modules():
    for path in sorted(PKG.rglob("*.py")):
        if "_hooks" in path.parts:
            continue
        yield path.relative_to(PKG).as_posix(), ast.parse(
            path.read_text(encoding="utf-8"))


def _functions(tree):
    """(enclosing function name, node) for every node, '<module>' at top."""
    def walk(node, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        yield func, node
        for child in ast.iter_child_nodes(node):
            yield from walk(child, func)
    yield from walk(tree, "<module>")


def _call_name(call):
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _calls_named(name, source):
    found = set()
    for mod, tree in source:
        for func, node in _functions(tree):
            if isinstance(node, ast.Call) and _call_name(node) == name:
                found.add((mod, func))
    return found


# ---- 1: project_slug ---------------------------------------------------------


def test_the_census_sees_a_project_slug_call_outside_the_owner():
    src = [("rogue.py", ast.parse("def f(p):\n    return store.project_slug(p)\n"))]
    assert _calls_named("project_slug", src) == {("rogue.py", "f")}


def test_no_site_derives_a_bucket_name_from_a_location_but_the_owner():
    found = _calls_named("project_slug", list(_modules()))
    assert found == {("store.py", "bucket_of")}, (
        "a bucket name must come from store.bucket_of / project_bucket / "
        "bucket_dir (or a claimed ref), never from project_slug on a "
        f"location: {sorted(found - {('store.py', 'bucket_of')})}")


# ---- 2: location_slug --------------------------------------------------------

SEED_SITES = {
    ("store.py", "project_slug"): "the pure transform itself",
    ("buckets.py", "legacy_slug"): "the pre-0.42.0 rule, by design a pure seed",
    ("buckets.py", "target_slug"): (
        "the migration's own target rule, kept pure; 4b guards `migrate` "
        "against the bound bucket"),
    ("importer.py", "check_project"): (
        "the host's transcript directory guess, a location fact"),
    ("cli/projects.py", "_cmd_slug"): "`daimon slug` prints the seed (#913)",
    ("bucket_tools.py", "stamp"): (
        "stamp matches an explicit scan directory's seed to a bucket name"),
    ("bucket_tools.py", "adopt"): (
        "adopt looks for the folder's OWN seed bucket (an unstamped legacy "
        "bucket the rebind would orphan): a seed is exactly what it needs"),
    ("identity.py", "resolve"): (
        "the resolver IS the owner's answer: the seed is its step 2 probe, "
        "its step 4 legacy directory and the first birth name"),
}


def test_a_seed_is_only_asked_for_by_the_sites_that_need_a_seed():
    found = _calls_named("location_slug", list(_modules()))
    unlisted = found - set(SEED_SITES)
    assert not unlisted, (
        "location_slug is the pure seed of a LOCATION (what `daimon slug` "
        "prints); a site that names a bucket asks store.bucket_of. Convert it "
        f"or list it with its reason: {sorted(unlisted)}")


def test_the_seed_allowlist_holds_no_stale_entry():
    found = _calls_named("location_slug", list(_modules()))
    stale = {k for k in set(SEED_SITES) - found if k != ("store.py", "project_slug")}
    assert not stale, sorted(stale)


# ---- 3: joins of the checkpoint root with a bucket name ----------------------

_OWNER = "the owner of a bucket path, or of one claimed ref's directory"
_FLAT = "a flat or global file at the checkpoint root, not a bucket"
_ENUM = ("joins a name the caller ENUMERATED from the checkpoint directory "
         "(or a legacy name by design), never one derived from a location")

_MERGE = ("merge and migrate work on two NAMED buckets inside one checkpoint "
          "directory: the names come from the two slug rules or the verb's "
          "arguments, and 4b guards `migrate` against the bound bucket")

JOIN_SITES = {
    ("store.py", "bucket_dir"): _OWNER,
    ("store.py", "ensure_bucket"): _OWNER + " (the ref's verified directory)",
    ("store.py", "global_latest_path"): _FLAT,
    ("store.py", "_scoped_read"): _FLAT + " (the global pointer)",
    ("store.py", "read_own_stream_latest"): _FLAT + " (the global pointer)",
    ("testing.py", "_require"): _FLAT + " (the test marker file)",
    ("privacy.py", "audit_project"): "the chunk cache directory, not a bucket",
    ("serializer.py", "_chunk_cache_dir"): "the chunk cache directory, not a bucket",
    ("buckets.py", "migrations_path"): _FLAT,
    ("buckets.py", "legacy_bucket"): "the legacy rule by design (pre-0.42.0 name)",
    ("buckets.py", "legacy_leftovers"): _ENUM,
    ("buckets.py", "merge"): _MERGE,
    ("buckets.py", "merge_buckets"): _MERGE,
    ("buckets.py", "migrate"): _MERGE,
    ("bucket_tools.py", "doctor"): _ENUM,
    ("bucket_tools.py", "_bucket_dir_named"): (
        "a verb argument that NAMES a bucket (validated by parse_bucket), the "
        "way merge names its two"),
    ("bucket_tools.py", "remap"): _ENUM,
    ("bucket_tools.py", "adopt"): (
        "joins the folder's own SEED name to find its legacy bucket, to refuse "
        "orphaning it; reads, never names where a write goes"),
    ("store.py", "make_bucket"): (
        "creates the bucket a verb NAMES (migrate folding into the rule's "
        "name); must not ask the resolver, which may answer another bucket"),
    ("identity.py", "_claim_once"): _OWNER + " (the claim's own directory)",
    ("identity.py", "claim"): _OWNER + " (a slug-routed bucket's directory)",
    ("identity.py", "verify"): _OWNER + " (the claimed ref's own directory)",
    ("ledger_census.py", "census_machine"): _FLAT,
    ("cli/status.py", "_admission_lines"): _ENUM,
    ("cli/status.py", "_status_ledgers"): _ENUM,
    ("view.py", "_is_bucket"): _ENUM,
    ("view.py", "_session_state"): _FLAT + " (a session file)",
    ("view.py", "receipt_state"): _FLAT + " (a session file)",
    ("receipts.py", "_checkpoint_file"): _FLAT + " (a session file)",
}


def _root_names(tree):
    """{function name: names bound from `checkpoint_dir()` inside it}."""
    names = collections.defaultdict(set)
    for func, node in _functions(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and _call_name(node.value) == "checkpoint_dir"):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names[func].add(target.id)
    return names


def _joins(source):
    found = set()
    for mod, tree in source:
        roots = _root_names(tree)
        for func, node in _functions(tree):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
                continue
            left = node.left
            while isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div):
                left = left.left
            direct = (isinstance(left, ast.Call)
                      and _call_name(left) == "checkpoint_dir")
            via_name = isinstance(left, ast.Name) and left.id in roots[func]
            if direct or via_name:
                found.add((mod, func))
    return found


def test_the_census_sees_a_join_through_a_name_bound_from_checkpoint_dir():
    src = [("rogue.py", ast.parse(
        "def f(slug):\n    d = config.checkpoint_dir()\n    return d / slug\n"))]
    assert _joins(src) == {("rogue.py", "f")}


def test_the_census_sees_a_direct_join():
    src = [("rogue.py", ast.parse(
        "def f(slug):\n    return config.checkpoint_dir() / slug / 'x'\n"))]
    assert _joins(src) == {("rogue.py", "f")}


def test_no_module_joins_the_checkpoint_root_with_a_bucket_name_outside_the_owner():
    unlisted = _joins(list(_modules())) - set(JOIN_SITES)
    assert not unlisted, (
        "a bucket path is store.bucket_dir(x) / name (or a claimed ref's "
        "directory); a join of checkpoint_dir() with a name derived here can "
        "diverge from the owner. Convert it, or list it with its reason: "
        f"{sorted(unlisted)}")


def test_the_join_allowlist_holds_no_stale_entry():
    stale = set(JOIN_SITES) - _joins(list(_modules()))
    assert not stale, (
        f"listed but no longer joining the checkpoint root: {sorted(stale)}")


# ---- 4: refs are built by identity.py only ----------------------------------


def test_a_bucket_ref_is_constructed_only_inside_identity():
    found = _calls_named("BucketRef", list(_modules()))
    assert {m for m, _ in found} == {"identity.py"}, sorted(found)


# ---- 5: no broad except absorbs BucketMismatch -------------------------------

# Entries every write path ends in. A function that calls one (or, transitively,
# a function with a unique name that does) is a claimer.
CLAIMERS = {"write_checkpoint", "append_event", "append_events",
            "append_verification", "append_world_outcomes",
            "record_forget_hits", "ensure_bucket", "claim",
            "_write_policy_tombstones", "attach_anchor", "capture.run",
            "_run_serialize"}
LEDGER_MODULES = {"refutations", "trust", "amendments", "requests",
                  "relations"}

ABSORBERS = {
    ("hooks.py", "on_session_end"): (
        "a hook never raises into the host session: the handler logs the "
        "traceback and writes a failure line to serialize.log, which `heal` "
        "retries, so the operation fails visibly and is repaired, not "
        "swallowed"),
}


def _key(call):
    f = call.func
    if isinstance(f, ast.Attribute):
        base = getattr(f.value, "id", "")
        if f.attr in ("append", "_append", "_write") and base in LEDGER_MODULES:
            return f"{base}.{f.attr}"
        if f.attr == "run" and base == "capture":
            return "capture.run"
        return f.attr
    return getattr(f, "id", None)


def _is_broad(handler):
    t = handler.type
    if t is None:
        return True
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    names = [x.attr if isinstance(x, ast.Attribute) else getattr(x, "id", "")
             for x in elts]
    return any(n in ("Exception", "BaseException") for n in names)


def _lets_it_through(try_node, handler):
    """A bare `raise` in the handler, or an earlier handler that names the
    identity errors and re-raises."""
    if any(isinstance(n, ast.Raise) and n.exc is None
           for n in ast.walk(handler)):
        return True
    for earlier in try_node.handlers:
        if earlier is handler:
            break
        text = ast.unparse(earlier.type) if earlier.type is not None else ""
        if ("LOUD" in text or "BucketMismatch" in text) and any(
                isinstance(n, ast.Raise) for n in ast.walk(earlier)):
            return True
    return False


def _absorbers(source):
    defs = collections.defaultdict(list)
    for mod, tree in source:
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                keys = {_key(c) for c in ast.walk(n)
                        if isinstance(c, ast.Call)} - {None}
                defs[n.name].append((mod, keys))
    reach = set(CLAIMERS) | {f"{m}.{a}" for m in LEDGER_MODULES
                             for a in ("append", "_append", "_write")}
    changed = True
    while changed:
        changed = False
        for name, where in defs.items():
            if (name not in reach and len(where) == 1 and len(name) >= 6
                    and where[0][1] & reach):
                reach.add(name)
                changed = True
    found = set()
    for mod, tree in source:
        for func, node in _functions(tree):
            if not isinstance(node, ast.Try):
                continue
            hit = any(isinstance(c, ast.Call) and _key(c) in reach
                      for stmt in node.body for c in ast.walk(stmt))
            if not hit:
                continue
            for handler in node.handlers:
                if _is_broad(handler) and not _lets_it_through(node, handler):
                    found.add((mod, func))
    return found


def test_the_census_sees_a_broad_except_around_a_claimer():
    src = [("rogue.py", ast.parse(
        "def f(p):\n    try:\n        store.write_checkpoint('s', {}, p)\n"
        "    except Exception:\n        pass\n"))]
    assert _absorbers(src) == {("rogue.py", "f")}


def test_the_census_accepts_a_handler_that_names_the_identity_errors_first():
    src = [("ok.py", ast.parse(
        "def f(p):\n    try:\n        store.write_checkpoint('s', {}, p)\n"
        "    except identity.LOUD:\n        raise\n"
        "    except Exception:\n        pass\n"))]
    assert _absorbers(src) == set()


def test_no_broad_except_around_a_claimer_absorbs_a_bucket_mismatch():
    unlisted = _absorbers(list(_modules())) - set(ABSORBERS)
    assert not unlisted, (
        "a broad `except` around a call that claims or verifies a bucket must "
        "name `identity.LOUD` first and re-raise (or be listed with the reason "
        f"it is loud some other way): {sorted(unlisted)}")


def test_the_absorber_allowlist_holds_no_stale_entry():
    stale = set(ABSORBERS) - _absorbers(list(_modules()))
    assert not stale, sorted(stale)
