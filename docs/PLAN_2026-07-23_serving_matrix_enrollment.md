# PLAN 2026-07-23 — Enroll holo3.1 in llama-swap's matrix (PROPOSED, external config)

_Not applied. This edits `/home/aaron/services/llama-swap/config.yaml`, which is
**outside this repo** (managed by `modelctl`, git-versioned separately). Written up as a
reviewable diff per the operator's request; apply by hand after reading, and back the
file up first per that project's own guardrail (`~/config-backups/<date>/`)._

## The problem, reproduced

`holo3.1` — the only model this project depends on — is **absent from llama-swap's
`matrix:` section**. Every other significant model has a `var`, an `evict_costs` entry,
and a `sets:` membership declaring what it may be resident alongside. holo3.1 has none.

This is not an oversight in `modelctl`; it is the one step `modelctl` deliberately does
not do. From `~/services/llama-swap/README.md`:

> "One thing modelctl does NOT do for you: `matrix:` membership. After adding a model,
> add it to `matrix:` by hand … This is a VRAM/eviction policy decision that needs human
> judgment, not something to auto-derive."

holo3.1 was added via `modelctl pull` and the manual follow-up never happened.

**Observed consequence (2026-07-23, both directions):**

| action | before | after |
|---|---|---|
| one `say ok` to `fast-7b` | `['holo3.1']` | `['fast-7b']` |
| `tools/serving_probe.py` warms holo3.1 | `['fast-7b']` | `['holo3.1']` |

So any other consumer of this box — the Hermes stack, another tool, a stray curl —
silently evicts the model mid-run. The reload costs **~13-17s measured**, and because
the client timeout is 180s it lands as *latency*, never an error.

**Has it bitten yet? No.** Every per-step wall time in the recorded battery archive that
exceeds its run's median by >12s is at **step 0** (35.9s, 37.4s, 31.0s — the battery's
first task paying a cold load). No mid-run eviction signature. The risk is real and
un-triggered, which is the cheapest possible moment to close it.

## Why holo3.1 can safely be co-resident (the README's blanket rule doesn't apply)

The README says GGUF models use `--tensor-split 4,1`, span both GPUs, and therefore
"evict the whole board." That is **stale for holo3.1**, whose live launch command is:

```
--split-mode none --tensor-split 4,1
```

`--split-mode none` makes llama.cpp use a **single** GPU; `--tensor-split` is inert in
that mode. holo3.1 occupies the **B70 (GPU.0) alone**, exactly like the OVMS big models
that already have `X & f7` co-residency sets. So it can follow the established pattern
rather than needing a new one.

## Proposed diff

Three additions, mirroring the existing `qwen_stack` / `gemma_stack` shape.

```diff
   matrix:
     vars:
       q27: big-qwen
       g31: big-gemma
+      holo: holo3.1
       ...
     evict_costs:
       q27: 1
       g31: 1
+      holo: 1
       ...
     sets:
       qwen_stack: q27 & f7
       gemma_stack: g31 & f7
+      holo_stack: holo & f7
       ...
```

Rationale for each:

- **`vars: holo: holo3.1`** — the `≤8 char, alphanumeric` var name the README requires.
- **`evict_costs: holo: 1`** — cost `1`, the big-model-pool default. holo3.1 *should* be
  evictable by another big model; the goal is not to pin it, but to make its eviction a
  declared policy decision rather than undefined behaviour. (Raise it to `50`, matching
  `f7`, only if you want batteries to be protected from all other use of the box — see
  the open question below.)
- **`sets: holo_stack: holo & f7`** — declares holo3.1 (B70) and fast-7b (B580) as a
  legal resident pair. This is what stops the mutual eviction above, and it is
  simultaneously the **Phase 5 prerequisite**: the roadmap's grounder/verifier tier wants
  a small model on the B580 co-resident with the planner on the B70, and this set is that
  arrangement, proven by the four identical stacks already in the config.

## Verification after applying

```bash
# 1. config still parses / service healthy
systemctl --user reload-or-restart llama-swap && systemctl --user status llama-swap

# 2. the pair is now legal: warm both, confirm BOTH stay resident
curl -s http://127.0.0.1:9292/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"fast-7b","messages":[{"role":"user","content":"ok"}],"max_tokens":1}' >/dev/null
curl -s http://127.0.0.1:9292/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"holo3.1","messages":[{"role":"user","content":"ok"}],"max_tokens":1}' >/dev/null
curl -s http://127.0.0.1:9292/running | python3 -m json.tool | grep '"model"'
# expect BOTH holo3.1 and fast-7b; before this change the second request evicted the first

# 3. from this repo, the same thing with a verdict + recorded artifact
python tools/serving_probe.py
```

`tools/serving_probe.py` records `co_resident` in `runs/serving_probe_<ts>/probe.json`,
so before/after states are on disk rather than in a terminal that scrolls away.

## Open question for the operator (not decided here)

**Should a battery pin holo3.1 (`evict_costs: 50`) for its duration?** A battery is a
measurement, and a mid-run eviction injects ~15s into one step's latency, which pollutes
`per_step_wall_time_s` — one of the roadmap's tracked metrics. Against that: pinning
starves everything else on the box for an hour, and the archive shows this has never
actually happened. Recommendation: **leave cost at `1`** and rely on
`tools/serving_probe.py` + the per-run `serving` snapshot now in `meta.json` to *detect*
it, since detection is now cheap and the event is hypothetical. Revisit if a run ever
records a mid-battery non-resident snapshot.

## What this does NOT change

`modelctl sync` regenerates model *entries* by name and "never touches the `matrix:`
section or any hand-authored entry not backed by a modelctl profile" (its README). So
this hand edit survives future `modelctl sync` runs — but note that sync round-trips the
whole YAML through a parser, stripping comments. Don't annotate the change inline; this
document is the annotation.
