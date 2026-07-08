import shutil
import subprocess

import pytest

from daimon_briefing import harvest


def test_detect_flags_avoidance_in_assistant_only():
    msgs = [
        {"role": "user", "content": "don't do the thing in app.py"},
        {"role": "assistant", "content": "Avoid calling resolve() twice in config.py."},
    ]
    hits = harvest.detect(msgs)
    assert len(hits) == 1
    assert hits[0].kind == "avoidance"
    assert hits[0].msg_index == 1


def test_detect_one_hit_per_marker_sentence():
    msgs = [{"role": "assistant",
             "content": "Never touch config.py mid-flush. Also avoid resolve() in store.py."}]
    hits = harvest.detect(msgs)
    assert len(hits) == 2
    assert all(h.kind == "avoidance" for h in hits)


def test_detect_flags_intentional():
    msgs = [{"role": "assistant", "content": "This cast looks wrong but is intentional."}]
    hits = harvest.detect(msgs)
    assert len(hits) == 1
    assert hits[0].kind == "intentional"


def test_detect_ignores_plain_prose():
    msgs = [{"role": "assistant", "content": "The function returns a list of paths."}]
    assert harvest.detect(msgs) == []


def test_detect_flattens_block_array_content():
    # Claude Code assistant content is often a block array, not a string.
    msgs = [{"role": "assistant", "content": [
        {"type": "text", "text": "Avoid calling resolve() twice in config.py."},
        {"type": "thinking", "text": "internal noise"},
        {"type": "tool_use", "name": "Edit", "input": {}},
    ]}]
    hits = harvest.detect(msgs)
    assert len(hits) == 1
    assert hits[0].kind == "avoidance"
    assert "resolve()" in hits[0].sentence


def test_detect_intentional_wins_within_a_sentence():
    # one sentence (no . ! ? separator) carrying BOTH an intentional and an avoidance marker
    msgs = [{"role": "assistant",
             "content": "This cast looks wrong but is intentional and we never change it"}]
    hits = harvest.detect(msgs)
    assert len(hits) == 1
    assert hits[0].kind == "intentional"


def test_detect_flags_spanish_avoidance():
    # Assistants reply in Spanish to Spanish-speaking users; the detector must
    # not go silent on them (#4).
    msgs = [{"role": "assistant",
             "content": "Evitá llamar resolve() dos veces en config.py. "
                        "Nunca toques config.py durante el flush."}]
    hits = harvest.detect(msgs)
    assert len(hits) == 2
    assert all(h.kind == "avoidance" for h in hits)


def test_detect_flags_spanish_breakage_and_deadend():
    msgs = [{"role": "assistant",
             "content": "Ese enfoque se rompió con sesiones reales. "
                        "Resultó ser un callejón sin salida."}]
    hits = harvest.detect(msgs)
    assert len(hits) == 2
    assert all(h.kind == "avoidance" for h in hits)


def test_detect_flags_spanish_intentional():
    msgs = [{"role": "assistant",
             "content": "Este cast parece incorrecto pero es intencional, debe quedarse."}]
    hits = harvest.detect(msgs)
    assert len(hits) == 1
    assert hits[0].kind == "intentional"


def test_detect_spanish_plain_prose_stays_silent():
    # "no" is far more frequent in Spanish than "don't" in English — plain
    # negation must not fire the avoidance class.
    msgs = [{"role": "assistant",
             "content": "La función no devuelve rutas absolutas. "
                        "El resultado no incluye duplicados."}]
    assert harvest.detect(msgs) == []


def test_anchor_of_returns_existing_path(tmp_path):
    (tmp_path / "config.py").write_text("x = 1\n")
    hit = harvest.Hit("avoidance", "Avoid calling resolve() twice in config.py.", "", 0)
    assert harvest.anchor_of(hit, str(tmp_path)) == "config.py"


def test_anchor_of_strips_line_suffix(tmp_path):
    (tmp_path / "config.py").write_text("x = 1\n")
    hit = harvest.Hit("avoidance", "Bug at config.py:42 breaks routing.", "", 0)
    assert harvest.anchor_of(hit, str(tmp_path)) == "config.py"


def test_anchor_of_returns_none_when_path_absent(tmp_path):
    hit = harvest.Hit("avoidance", "Never do the thing, it breaks.", "", 0)
    assert harvest.anchor_of(hit, str(tmp_path)) is None


def test_anchor_of_returns_none_when_path_nonexistent(tmp_path):
    hit = harvest.Hit("avoidance", "Avoid the trap in ghost.py, it breaks.", "", 0)
    assert harvest.anchor_of(hit, str(tmp_path)) is None


def test_anchor_of_rejects_absolute_path(tmp_path):
    # a real file that exists, referenced by ABSOLUTE path, must NOT anchor (outside repo-relative contract)
    target = tmp_path / "outside.py"
    target.write_text("x = 1\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    hit = harvest.Hit("avoidance", f"Avoid the bug in {target} it breaks.", "", 0)
    assert harvest.anchor_of(hit, str(proj)) is None


def test_anchor_of_rejects_parent_traversal(tmp_path):
    # a real file one dir ABOVE the project root, referenced via ../, must NOT anchor
    (tmp_path / "secret.md").write_text("x\n")
    proj = tmp_path / "proj"
    proj.mkdir()
    hit = harvest.Hit("avoidance", "Never read ../secret.md, it breaks isolation.", "", 0)
    assert harvest.anchor_of(hit, str(proj)) is None


def _parse_frontmatter(md):
    import re as _re
    block = _re.match(r"---\n(.*?)\n---", md, _re.DOTALL).group(1)
    fm = {}
    for line in block.splitlines():
        if line and not line.startswith(" ") and ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, block


def test_to_candidate_builds_valid_frontmatter():
    hit = harvest.Hit("avoidance", "Never route by raw cwd in config.py.", "", 0)
    slug, md = harvest.to_candidate(hit, "config.py", "S-42", "2026-06-30")
    fm, block = _parse_frontmatter(md)
    assert fm["type"] == "landmine"
    assert fm["status"] == "candidate"
    assert fm["created"] == "2026-06-30"
    assert "path: config.py" in block
    assert "S-42" in md
    assert slug and slug == slug.lower()


def test_to_candidate_intentional_is_fence():
    hit = harvest.Hit("intentional", "This cast looks wrong but is intentional in x.py.", "", 0)
    _, md = harvest.to_candidate(hit, "x.py", "S1", "2026-06-30")
    assert _parse_frontmatter(md)[0]["type"] == "fence"


def test_to_candidate_tried_and_failed_is_deadend():
    hit = harvest.Hit("avoidance", "We tried the mmap approach in store.py; it doesn't work.", "", 0)
    _, md = harvest.to_candidate(hit, "store.py", "S1", "2026-06-30")
    assert _parse_frontmatter(md)[0]["type"] == "deadend"


def test_to_candidate_title_with_colon_is_yaml_safe():
    hit = harvest.Hit("avoidance", "Gotcha: never touch cache.py mid-flush.", "", 0)
    _, md = harvest.to_candidate(hit, "cache.py", "S1", "2026-06-30")
    fm, _ = _parse_frontmatter(md)
    assert fm["title"].startswith('"')  # json.dumps quoting keeps YAML valid


def test_to_candidate_redacts_secret_in_body_and_title():
    # #109: candidate files are committable — a verbatim assistant sentence
    # carrying a secret must be scrubbed before it becomes the candidate's
    # title, slug, and body.
    hit = harvest.Hit(
        "avoidance",
        "Never hardcode sk_live_a1B2c3D4e5F6g7H8 in config.py.",
        "", 0,
    )
    slug, md = harvest.to_candidate(hit, "config.py", "S1", "2026-06-30")
    assert "sk_live_a1B2c3D4e5F6g7H8" not in md
    assert "[redacted:stripe-key]" in md
    assert "sk_live" not in slug


def _assistant(text):
    return {"role": "assistant", "content": text}


def _scars_repo(tmp_path):
    (tmp_path / ".scars").mkdir()
    (tmp_path / "config.py").write_text("x = 1\n")
    return tmp_path


def test_run_writes_anchored_candidate(tmp_path):
    root = _scars_repo(tmp_path)
    n = harvest.run([_assistant("Never route by raw cwd in config.py.")], str(root), "S1")
    assert n == 1
    files = list((root / ".scars" / "candidates").glob("*.md"))
    assert len(files) == 1
    assert "status: candidate" in files[0].read_text()


def test_run_is_idempotent(tmp_path):
    root = _scars_repo(tmp_path)
    msgs = [_assistant("Never route by raw cwd in config.py.")]
    assert harvest.run(msgs, str(root), "S1") == 1
    assert harvest.run(msgs, str(root), "S1") == 0  # existing file not overwritten
    assert len(list((root / ".scars" / "candidates").glob("*.md"))) == 1


def test_run_skips_unanchored_hits(tmp_path):
    root = _scars_repo(tmp_path)
    n = harvest.run([_assistant("Never do the risky thing, it breaks everything.")], str(root), "S1")
    assert n == 0


def test_run_skips_when_no_scars_dir(tmp_path):
    (tmp_path / "config.py").write_text("x = 1\n")  # no .scars/
    assert harvest.run([_assistant("Never touch config.py.")], str(tmp_path), "S1") == 0


def test_run_skips_when_project_root_falsy():
    assert harvest.run([_assistant("Never touch config.py.")], None, "S1") == 0


def test_run_caps_at_five(tmp_path):
    root = _scars_repo(tmp_path)
    for i in range(7):
        (root / f"f{i}.py").write_text("x\n")
    msgs = [_assistant(f"Gotcha number {i}: never touch f{i}.py mid-run.") for i in range(7)]
    assert harvest.run(msgs, str(root), "S1") == 5


def test_emitted_candidate_passes_scar_lint(tmp_path):
    if shutil.which("scar") is None:
        pytest.skip("scar binary not installed")
    (tmp_path / ".scars").mkdir()
    (tmp_path / "config.py").write_text("x = 1\n")
    (tmp_path / ".scars" / "template.md").write_text("---\nstatus: template\n---\n")
    n = harvest.run([_assistant("Never route by raw cwd in config.py.")], str(tmp_path), "S1")
    assert n == 1
    out = subprocess.run(["scar", "lint"], cwd=tmp_path, capture_output=True, text=True)
    assert "with errors" not in out.stdout or "0 with errors" in out.stdout
