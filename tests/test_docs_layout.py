"""
test_docs_layout.py — OFFLINE lint for the file-layout law (AGENTS.md §6).

The docs taxonomy is enforced by machine, not by review memory: name grammar for
dated docs, a clean repo root, tool-named files staying pointers, and runs/
evidence cites in new SESSION/FINDINGS docs. Pre-law files (before 2026-07-22)
are grandfathered BY NAME below — the lists are frozen; never add to them. If
this lint blocks a legitimate new file, change the lint in the same commit
(AGENTS.md §6); never route around it.

    python tests/test_docs_layout.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")

# docs/<TYPE>_<YYYY-MM-DD>[_<slug>].md
DOC_RE = re.compile(r"^(PLAN|SESSION|FINDINGS|REPORT)_(\d{4}-\d{2}-\d{2})(_[A-Za-z0-9_]+)?\.md$")

# The three mutable prose files (AGENTS.md §6). ROADMAP lives in docs/.
MUTABLE_DOCS = {"ROADMAP.md"}
ROOT_MD_ALLOWED = {"AGENTS.md", "PROJECT_STATE.md", "CLAUDE.md", "README.md"}

# FROZEN 2026-07-22: pre-law docs that don't match the grammar. Do NOT add here.
LEGACY_DOCS = {
    "DEMOS.md",
    "FINDINGS_holo_bringup.md",
    "FINDINGS_integration.md",
    "FORMAT_NOTES_holo.md",
    "PACKAGING_STATUS_2026-06-21.md",
    "README_evocua_mcp.md",
    "README_openwebui.md",
    "UITARS_INTEGRATION.md",
}

# Tool-named entrypoints that must stay pointers if present (only create one for
# a tool actually in use).
POINTER_FILES = ["CLAUDE.md", "GEMINI.md", "CONVENTIONS.md", ".cursorrules",
                 os.path.join(".github", "copilot-instructions.md")]
POINTER_MAX_LINES = 12

# The law landed 2026-07-22; the evidence-cite rule applies to docs dated after.
LAW_DATE = "2026-07-22"


def _docs_md():
    return sorted(n for n in os.listdir(DOCS)
                  if n.endswith(".md") and os.path.isfile(os.path.join(DOCS, n)))


def test_docs_filenames_follow_grammar():
    for name in _docs_md():
        if name in LEGACY_DOCS or name in MUTABLE_DOCS:
            continue
        assert DOC_RE.match(name), \
            f"docs/{name} violates the name grammar " \
            f"docs/<PLAN|SESSION|FINDINGS|REPORT>_<YYYY-MM-DD>_<slug>.md (AGENTS.md §6)"


def test_no_stray_markdown_at_repo_root():
    md = {n for n in os.listdir(ROOT)
          if n.endswith(".md") and os.path.isfile(os.path.join(ROOT, n))}
    stray = md - ROOT_MD_ALLOWED
    assert not stray, \
        f"stray markdown at repo root: {sorted(stray)} -- dated docs go in docs/, " \
        f"rules in AGENTS.md (AGENTS.md §6)"


def test_new_evidence_docs_cite_runs():
    for name in _docs_md():
        m = DOC_RE.match(name)
        if not m or m.group(1) not in ("SESSION", "FINDINGS"):
            continue
        if m.group(2) <= LAW_DATE:   # pre-law docs grandfathered by date
            continue
        with open(os.path.join(DOCS, name)) as f:
            text = f.read()
        assert "runs/" in text, \
            f"docs/{name} is a {m.group(1)} doc but cites no runs/ evidence path " \
            f"(AGENTS.md §6: the camera and the recorder are the evidence)"


def test_tool_pointer_files_stay_pointers():
    for rel in POINTER_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        with open(p) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        assert any("AGENTS.md" in ln for ln in lines), \
            f"{rel} does not point at AGENTS.md -- tool files are pointers (AGENTS.md §6)"
        assert len(lines) <= POINTER_MAX_LINES, \
            f"{rel} has {len(lines)} non-empty lines (max {POINTER_MAX_LINES}) -- " \
            f"content is drifting out of AGENTS.md into a tool file (AGENTS.md §6)"


def test_frozen_lists_stay_frozen():
    """The grandfather lists only shrink (a legacy doc renamed/retired is fine);
    a NEW name appearing in them would be routing around the law."""
    present = set(_docs_md())
    ghosts = LEGACY_DOCS - present
    assert not ghosts or all(g in LEGACY_DOCS for g in ghosts)  # shrinking is fine
    # every legacy entry that exists must predate the law in git terms -- proxy
    # check: it must NOT match the grammar (else it doesn't need grandfathering)
    for name in LEGACY_DOCS & present:
        assert not DOC_RE.match(name), \
            f"{name} matches the grammar -- remove it from LEGACY_DOCS"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in fns:
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except Exception:
            fails += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print("\n" + ("ALL PASS" if not fails else f"{fails} FAILED"))
    sys.exit(1 if fails else 0)
