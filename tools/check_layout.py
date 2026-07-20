"""
check_layout.py -- mechanical guard against the exact sprawl fixed on 2026-07-19 (see
docs/PROJECT_LAYOUT.md). Before that consolidation, FOUR independently-invented path
schemes existed for generated/runtime output (runs/, logs/, waa/results+cache, _dbg/)
purely because nothing ever checked for a new one being invented -- a doc alone
(CLAUDE.md's old "WHERE TO SAVE NEW FILES" section) did not prevent this drift; this
script is the structural fix. Wired as a pre-commit gate two ways: a committed
.claude/settings.json PreToolUse hook (fires automatically for any Claude Code session
on this repo, no install step) and tools/install_hooks.sh (a real git pre-commit hook,
for commits made from a plain terminal where the Claude Code hook can't fire).

Flags exactly the anti-pattern this repo's real violations shared, found by grepping
the tracked tree on 2026-07-19 -- same definition here as was used to find and fix them,
so there's no drift between "what the consolidation fixed" and "what this catches":

  A. hardcoded-path RECONSTRUCTION: dirname(...abspath(__file__)...) combined with a
     literal for one of the now-consolidated names, instead of importing CFG.
  B. bare STRING-LITERAL data paths for one of those same names, used outside
     kvm_agent/config.py (the one file allowed to know these names at all).
  C. an undocumented new TOP-LEVEL directory not in the allowlist below.

Run standalone anytime:  python3 tools/check_layout.py
Exits nonzero with a message pointing at docs/PROJECT_LAYOUT.md on any violation.
"""
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The literal names that must ONLY be reached via kvm_agent.config.CFG's var_dir-derived
# properties (runs_dir, logs_dir, waa_results_dir, waa_cache_dir, waa_shakedown_dir,
# dbg_dir, scratch_dir) -- see kvm_agent/config.py.
CONSOLIDATED_NAMES = [
    "runs", "logs", "_dbg", "scratch",
    "waa/cache", "waa/results", "waa/shakedown_results",
    "waa_cache", "waa_results", "waa_shakedown",
]

# Files allowed to reference the consolidated names directly -- either because they ARE
# the single source of truth (kvm_agent/config.py), or because they're pre-2026-07-19
# legacy shim-era tools explicitly left untouched by the consolidation (see
# docs/PROJECT_LAYOUT.md "known exceptions" -- delete an entry here once its tool is
# migrated or retired, don't just grow this list).
EXEMPT_FILES = {
    "kvm_agent/config.py",
    "tools/check_layout.py",  # defines CONSOLIDATED_NAMES itself -- not a violation
    "live_ctl.py",
    "tools/operate.py",
    "tools/run_probe.py",
    "tools/eval_harness.py",
}
EXEMPT_PREFIXES = ("_archive/",)

# Top-level tracked entries this repo is allowed to have. A new one triggers anti-pattern
# C -- add to this list AND to docs/PROJECT_LAYOUT.md in the same commit, deliberately,
# rather than letting a new scattered location appear silently.
TOP_LEVEL_ALLOWLIST = {
    "kvm_agent", "waa", "appliance", "tools", "probes", "tests", "docs", "_archive",
    ".claude", "pico_w",
}

_DIRNAME_RECONSTRUCTION = re.compile(r"dirname\([^)]*abspath\(__file__\)")


def _tracked_py_files():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO_ROOT,
                          capture_output=True, text=True, check=True)
    return [f for f in out.stdout.splitlines() if f]


def _is_exempt(relpath):
    return relpath in EXEMPT_FILES or relpath.startswith(EXEMPT_PREFIXES)


def check_hardcoded_paths():
    """Anti-patterns A and B together: any tracked, non-exempt .py file that mentions one
    of the consolidated names as a string literal is a violation, regardless of whether
    it's paired with a dirname/abspath reconstruction or just a bare literal -- both were
    real instances of the same underlying mistake (see docs/PROJECT_LAYOUT.md)."""
    violations = []
    name_pattern = re.compile(
        r"""["'](?:%s)["']""" % "|".join(re.escape(n) for n in CONSOLIDATED_NAMES)
    )
    for relpath in _tracked_py_files():
        if _is_exempt(relpath):
            continue
        abspath = os.path.join(REPO_ROOT, relpath)
        try:
            with open(abspath, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if name_pattern.search(line):
                violations.append((relpath, lineno, line.strip()))
    return violations


def check_top_level_dirs():
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT,
                          capture_output=True, text=True, check=True)
    top_dirs = set()
    for f in out.stdout.splitlines():
        if "/" in f:
            top_dirs.add(f.split("/", 1)[0])
    return sorted(top_dirs - TOP_LEVEL_ALLOWLIST)


def main():
    problems = []

    path_violations = check_hardcoded_paths()
    if path_violations:
        problems.append(
            "Hardcoded reference(s) to a consolidated output path -- use "
            "kvm_agent.config.CFG's var_dir-derived properties instead "
            "(CFG.runs_dir, CFG.logs_dir, CFG.waa_results_dir, CFG.waa_cache_dir, "
            "CFG.waa_shakedown_dir, CFG.dbg_dir, CFG.scratch_dir). See "
            "docs/PROJECT_LAYOUT.md."
        )
        for relpath, lineno, line in path_violations:
            problems.append(f"  {relpath}:{lineno}: {line}")

    new_dirs = check_top_level_dirs()
    if new_dirs:
        problems.append(
            "Undocumented new top-level director{}: {} -- add to "
            "tools/check_layout.py's TOP_LEVEL_ALLOWLIST AND docs/PROJECT_LAYOUT.md "
            "in the same commit if this is intentional.".format(
                "y" if len(new_dirs) == 1 else "ies", ", ".join(new_dirs)
            )
        )

    if problems:
        print("check_layout.py: FAILED\n")
        print("\n".join(problems))
        print("\nSee docs/PROJECT_LAYOUT.md for the canonical layout and how to fix this.")
        return 1

    print("check_layout.py: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
