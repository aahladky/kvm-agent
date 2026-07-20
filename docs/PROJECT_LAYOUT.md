# Project layout (canonical, kept current)

This is the single source of truth for where anything project-related lives. Written
2026-07-19 after generated/runtime output was found scattered across four
independently-invented path schemes (`runs/`, a hand-reconstructed `logs/`, `waa/`'s own
`results/`+`cache/` mixed in with its source code, and `agent_loop_holo.py`'s
script-relative `_dbg/`) plus a remote Pi 5, an external git clone, and a Claude Code
job-tmp directory nobody could find without being told the exact path.

CLAUDE.md previously had its own "WHERE TO SAVE NEW FILES GOING FORWARD" section written
2026-06-21 — it didn't prevent the drift above, because it was a second copy of this
information that went stale (it mentioned `scratch/`, which was never actually created;
it never mentioned `logs/`, `waa/cache`, `waa/results`, or `_dbg` at all). CLAUDE.md now
just points here instead of duplicating it. **This is the one doc to update when the
layout changes** — don't write a second copy anywhere else.

## The rule

**Every path for generated/runtime output goes through `kvm_agent.config.CFG`.** Never
hardcode a `"runs"`/`"logs"`/`"scratch"`-style string literal, and never reconstruct a
path via `os.path.dirname(os.path.abspath(__file__))` chains to reach one. If you need a
new kind of output location, add a property to `CFG` (see `kvm_agent/config.py`'s
`var_dir`-derived properties) — don't invent a fifth ad hoc scheme.

`tools/check_layout.py` enforces this mechanically at commit time (see below) — it isn't
just a request, a future session that violates it will have the commit blocked.

## Directory layout

```
var/                  # ← THE root for everything generated/runtime. gitignored as a whole.
├── runs/              #   per-run step frames + JSON (RunRecorder, WAA runs, REPL run())
├── logs/               #   holo_requests.jsonl, appliance_client_commands.jsonl
├── waa_results/         #   WAA batch pass/fail summaries (waa/runner.py's RESULTS_DIR)
├── waa_cache/            #   WAA setup/eval scratch state (waa/runner.py's CACHE_DIR)
├── waa_shakedown/         #   tools/shakedown_ab.py's multi-depth calibration batches
├── dbg/                    #   agent_loop_holo.py's REPL debug frames (cap()/mark())
└── scratch/                 #   throwaway output, dated subdirs: YYYY-MM-DD_<topic>/
```

`models/` (gitignored, ~35GB of model weight blobs) stays at the repo top level, outside
`var/` — already unambiguous, no reason to move huge binaries for zero benefit.

`kvm_agent/`, `waa/` (now code-only — `results/`/`cache/` moved out to `var/`),
`appliance/`, `tools/`, `probes/`, `tests/`, `docs/`, `_archive/` are git-tracked source,
unaffected by any of this.

## The CFG fields (`kvm_agent/config.py`)

One real field, `CFG.var_dir` (env-overridable via `VAR_DIR`, defaults to
`<repo_root>/var`), plus a `@property` per purpose — each reads `var_dir` live and is
still individually env-overridable for back-compat:

| Property | Path | Env override |
|---|---|---|
| `CFG.runs_dir` | `var/runs` | `RUNS_DIR` |
| `CFG.logs_dir` | `var/logs` | `LOGS_DIR` |
| `CFG.waa_results_dir` | `var/waa_results` | `WAA_RESULTS_DIR` |
| `CFG.waa_cache_dir` | `var/waa_cache` | `WAA_CACHE_DIR` |
| `CFG.waa_shakedown_dir` | `var/waa_shakedown` | `WAA_SHAKEDOWN_DIR` |
| `CFG.dbg_dir` | `var/dbg` | `DBG_DIR` |
| `CFG.scratch_dir` | `var/scratch` | `SCRATCH_DIR` |

`kvm_agent/instrumentation/run_log.py`'s `RunRecorder` (`os.path.join(CFG.runs_dir, ...)`)
is the reference pattern every consumer should match.

## `var/scratch/` convention

Dated subdirectories, not a flat dump: `var/scratch/YYYY-MM-DD_<short-topic>/`. If you're
about to write throwaway output somewhere and it's not through `CFG`, stop — that's
exactly the excuse that produced the sprawl this doc exists to prevent.

## What can't physically live under `var/`

See `docs/EXTERNAL_DEPS.md` for the remote Pi 5 appliance log and the external
WindowsAgentArena clone — both documented there instead of being tribal knowledge.

## Known exceptions

Four pre-2026-07-19 legacy shim-era tools were deliberately left untouched by this
consolidation (per "don't touch the flat-root shim files" from an earlier session) even
though they still hardcode scattered paths directly: `live_ctl.py`, `tools/operate.py`,
`tools/run_probe.py`, `tools/eval_harness.py`. `.gitignore` keeps a small defensive block
(`runs/`, `_dbg/`) specifically so these tools don't recreate an un-ignored directory at
the old top-level location when run. `tools/check_layout.py`'s `EXEMPT_FILES` list
matches this exception set exactly — remove an entry from both places together once its
tool is migrated to `CFG` or retired, don't let the exception list grow.

## The mechanical guard

`tools/check_layout.py` — run it standalone anytime (`python3 tools/check_layout.py`).
Flags: (A) hardcoded reconstruction of a consolidated path, (B) bare string-literal
references to a consolidated name outside `kvm_agent/config.py`, (C) an undocumented new
top-level tracked directory. Wired two ways:

1. `.claude/settings.json` — a committed `PreToolUse` hook that runs the check before any
   `git commit` made through a Claude Code session on this repo. Ships in the tree, no
   install step, which is the actual point: a future session with zero memory of this
   doc still can't land a commit that reintroduces the sprawl.
2. `tools/install_hooks.sh` — one-time opt-in, installs a real `.git/hooks/pre-commit`
   calling the same check, for commits made from a plain terminal where the Claude Code
   hook can't fire (`.git/hooks/` is never itself tracked by git).

Neither layer catches scatter into `/tmp` or a Claude Code job-tmp directory outside the
repo entirely — that's exactly what happened during the 2026-07-19 investigation that
prompted this doc. The real mitigation for that class is `CFG.scratch_dir`: a future
session has an obvious, one-import sanctioned place to write throwaway output, removing
the excuse to invent something new.
